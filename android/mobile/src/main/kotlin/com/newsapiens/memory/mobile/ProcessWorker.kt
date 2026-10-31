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
import java.time.LocalDate
import java.time.ZoneId
import java.util.concurrent.TimeUnit
import kotlinx.coroutines.flow.first
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.MultipartBody
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.asRequestBody
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONArray
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
                val at = { off: Long? -> id + (off ?: 0) }  // speech time = recording start + offset; never processing time
                dao.saveResult(
                    cap.copy(status = "ready", error = null, durationS = r.durationS, language = r.language, title = r.title,
                        summary = r.summary, topics = r.topics.joinToString(", "), transcript = r.transcript,
                        processedAt = System.currentTimeMillis(), speechS = r.speechS),
                    r.moments.map { Moment(captureId = id, category = it.category, title = it.title, text = it.text, atMs = at(it.offsetMs), importance = it.importance, line = it.line) },
                    r.events.map { Event(captureId = id, title = it.title, description = it.description, category = it.category,
                        startMs = at(it.startMs), endMs = at(it.endMs), importance = it.importance, line = it.line) },
                    r.tasks.map { ActionItem(captureId = id, title = it.title, assignee = it.assignee, dueDate = it.dueDate, dueText = it.dueText,
                        priority = it.priority, description = it.description, atMs = at(it.offsetMs), line = it.line, dueTime = it.dueTime) },
                )
                DayWorker.enqueue(ctx, dayOf(id))
                file.delete()
                if (CalendarSync.hasPermission(ctx)) CalendarSync.addDue(ctx, dao, dao.unsyncedDue(id))
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

/** Rebuilds one day's "at a glance" summary from its already-summarised captures via POST /api/day. Enqueued after every
 *  processed capture with a 2-minute delay that each new capture resets, so a burst of segments costs one call. */
class DayWorker(private val ctx: Context, p: WorkerParameters) : CoroutineWorker(ctx, p) {
    override suspend fun doWork(): Result {
        val date = LocalDate.parse(inputData.getString("date") ?: return Result.success())
        val dao = Db.get(ctx).dao()
        val (from, to) = dayRange(date)
        val caps = dao.capturesBetween(from, to).first().filter { it.status == "ready" }
        val events = dao.eventsBetween(from, to).first()
        val items = dayItems(caps, events)
        // ponytail: count-based staleness check; a re-processed capture with the same count keeps the old summary until the next one
        if (items.isEmpty() || dao.summaryNow(date.toString())?.sourceCount == caps.size) return Result.success()
        val top = events.filter { it.importance >= 0.5 }.sortedByDescending { it.importance }.take(5).joinToString("\n") { it.title }
        val body = JSONObject().put("today", date.toString()).put("items", JSONArray(items)).toString().toRequestBody("application/json".toMediaType())
        val req = Request.Builder().url("${BuildConfig.BACKEND_URL}/api/day").post(body).build()
        val (code, text) = try {
            ProcessWorker.client.newCall(req).execute().use { it.code to (it.body?.string() ?: "") }
        } catch (e: IOException) {
            return if (runAttemptCount < 3) Result.retry() else Result.success()
        }
        val (summary, highlights) = when {
            code in 200..299 -> try {
                val j = JSONObject(text)
                val h = j.optJSONArray("highlights")
                j.optString("summary") to (0 until (h?.length() ?: 0)).joinToString("\n") { h!!.getString(it) }
            } catch (e: JSONException) { "" to top }
            code == 503 -> "" to top  // no LLM on the server: the dashboard lists the day's main events instead of prose
            else -> return if (runAttemptCount < 3) Result.retry() else Result.success()
        }
        dao.saveSummary(DailySummary(date.toString(), summary, highlights, caps.size, System.currentTimeMillis()))
        return Result.success()
    }

    companion object {
        fun enqueue(ctx: Context, date: LocalDate) {
            val req = OneTimeWorkRequestBuilder<DayWorker>()
                .setInputData(Data.Builder().putString("date", date.toString()).build())
                .setInitialDelay(2, TimeUnit.MINUTES)
                .setConstraints(Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build())
                .setBackoffCriteria(BackoffPolicy.EXPONENTIAL, 30, TimeUnit.SECONDS).build()
            WorkManager.getInstance(ctx).enqueueUniqueWork("day-$date", ExistingWorkPolicy.REPLACE, req)
        }
    }
}
