package com.newsapiens.memory.wear

import android.content.Context
import androidx.work.CoroutineWorker
import androidx.work.ExistingWorkPolicy
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.WorkerParameters
import com.google.android.gms.wearable.Wearable
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.tasks.await

/** Sends every finished recording to the phone. A file is only deleted when the phone acks it (AckService),
 *  so a missed ack just means a resend, which the phone de-duplicates by capture id. */
class SendWorker(private val ctx: Context, p: WorkerParameters) : CoroutineWorker(ctx, p) {
    override suspend fun doWork(): Result {
        val files = Recorder.ready(ctx)
        if (files.isEmpty()) return Result.success()
        return try {
            val node = Wearable.getNodeClient(ctx).connectedNodes.await().firstOrNull { it.isNearby }
                ?: return Result.retry()  // phone not connected: try again with backoff
            val channels = Wearable.getChannelClient(ctx)
            for (f in files) {
                val ch = channels.openChannel(node.id, Recorder.PATH_RECORDING + f.name).await()
                try {
                    channels.getOutputStream(ch).await().use { out -> f.inputStream().use { it.copyTo(out) } }
                } finally {
                    channels.close(ch).await()
                }
            }
            Result.success()
        } catch (e: CancellationException) {
            throw e
        } catch (e: Exception) {
            Result.retry()  // Data Layer unavailable, phone dropped mid-transfer, ...: the recording stays on the watch
        }
    }

    companion object {
        fun enqueue(ctx: Context) {
            WorkManager.getInstance(ctx).enqueueUniqueWork("send", ExistingWorkPolicy.REPLACE, OneTimeWorkRequestBuilder<SendWorker>().build())
        }
    }
}
