package com.newsapiens.memory.mobile

import android.content.Context
import androidx.work.BackoffPolicy
import androidx.work.Constraints
import androidx.work.CoroutineWorker
import androidx.work.Data
import androidx.work.ExistingWorkPolicy
import androidx.work.NetworkType
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.WorkerParameters
import java.io.IOException
import java.time.Instant
import java.time.ZoneId
import java.util.concurrent.TimeUnit
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.MultipartBody
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.asRequestBody
import org.json.JSONException
import org.json.JSONObject

/** Uploads one recording to the backend, stores the result, then deletes the audio. */
class ProcessWorker(private val ctx: Context, p: WorkerParameters) : CoroutineWorker(ctx, p) {
    override suspend fun doWork(): Result {
        val id = inputData.getLong("id", 0)
        val dao = Db.get(ctx).dao()
        val cap = dao.capture(id) ?: return Result.success()
        suspend fun fail(msg: String): Result { dao.updateCapture(cap.copy(status = "failed", error = msg)); return Result.success() }

        val file = Files.audio(ctx, id)
        if (!file.exists()) return fail("Audio file is missing")

        dao.updateCapture(cap.copy(status = "processing", error = null))
        val occurred = Instant.ofEpochMilli(id).atZone(ZoneId.systemDefault()).toLocalDateTime().toString()
        val body = MultipartBody.Builder().setType(MultipartBody.FORM)
            .addFormDataPart("file", file.name, file.asRequestBody("audio/mp4".toMediaType()))
            .addFormDataPart("occurred_at", occurred).build()
        val req = Request.Builder().url("${BuildConfig.BACKEND_URL}/api/process").post(body).build()

        val (code, text) = try {
            client.newCall(req).execute().use { it.code to (it.body?.string() ?: "") }
        } catch (e: IOException) {
            return retryOrFail(cap, "Network error: ${e.message}")
        } catch (e: IllegalArgumentException) {
            return fail("Invalid backend URL")
        }
        return when {
            code in 200..299 -> try {
                val r = parseProcess(text)
                dao.saveResult(
                    cap.copy(status = "ready", error = null, durationS = r.durationS, language = r.language,
                        summary = r.summary, topics = r.topics.joinToString(", "), transcript = r.transcript),
                    r.decisions.map { Decision(captureId = id, text = it) },
                    r.tasks.map { ActionItem(captureId = id, title = it.title, assignee = it.assignee, dueDate = it.dueDate, dueText = it.dueText, priority = it.priority) },
                )
                file.delete()
                if (CalendarSync.hasPermission(ctx)) CalendarSync.addDue(ctx, dao, id)
                Result.success()
            } catch (e: JSONException) {
                fail("Unreadable server response")
            }
            code == 429 || code >= 500 -> retryOrFail(cap, "Server busy ($code): ${detail(text)}")
            else -> fail("Server said $code: ${detail(text)}")
        }
    }

    private suspend fun retryOrFail(cap: Capture, msg: String): Result {
        val dao = Db.get(ctx).dao()
        if (runAttemptCount >= 5) { dao.updateCapture(cap.copy(status = "failed", error = msg)); return Result.success() }
        dao.updateCapture(cap.copy(status = "queued", error = msg))
        return Result.retry()
    }

    private fun detail(body: String) = try { JSONObject(body).optString("detail", body) } catch (e: JSONException) { body }.take(200)

    companion object {
        val client = OkHttpClient.Builder().readTimeout(180, TimeUnit.SECONDS).writeTimeout(180, TimeUnit.SECONDS).build()

        fun enqueue(ctx: Context, id: Long) {
            val req = OneTimeWorkRequestBuilder<ProcessWorker>()
                .setInputData(Data.Builder().putLong("id", id).build())
                .setConstraints(Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build())
                .setBackoffCriteria(BackoffPolicy.EXPONENTIAL, 30, TimeUnit.SECONDS).build()
            WorkManager.getInstance(ctx).enqueueUniqueWork("process-$id", ExistingWorkPolicy.REPLACE, req)
        }
    }
}
