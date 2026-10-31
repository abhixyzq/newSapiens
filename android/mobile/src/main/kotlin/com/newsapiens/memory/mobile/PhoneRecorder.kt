package com.newsapiens.memory.mobile

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.media.MediaRecorder
import android.os.Build
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import androidx.core.app.ServiceCompat
import androidx.core.content.ContextCompat
import java.io.File
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.runBlocking

data class RecState(val recording: Boolean = false, val startedAt: Long = 0, val levels: List<Float> = emptyList())

/** Records on the phone in a microphone foreground service, so capture keeps going with the app closed until Stop.
 *  Every finished segment goes down the same path as a watch recording: Capture row + ProcessWorker. */
class PhoneRecorder : Service() {
    private var rec: MediaRecorder? = null
    private var part: File? = null
    private val handler = Handler(Looper.getMainLooper())
    private val roll = Runnable { finishSegment(); beginSegment() }
    private val sample = object : Runnable {
        override fun run() {
            val amp = try { rec?.maxAmplitude ?: 0 } catch (e: IllegalStateException) { 0 }
            val s = _state.value
            _state.value = s.copy(levels = (s.levels + Math.sqrt(amp / 32767.0).toFloat()).takeLast(LEVELS))
            handler.postDelayed(this, 100)
        }
    }

    override fun onBind(i: Intent?): IBinder? = null

    override fun onStartCommand(i: Intent?, flags: Int, startId: Int): Int {
        if (i?.action == ACTION_STOP) stop() else start()
        return START_NOT_STICKY  // Android 14+ won't let a mic service restart from the background anyway
    }

    private fun start() {
        if (rec != null) return
        val nm = getSystemService(NotificationManager::class.java)
        nm.createNotificationChannel(NotificationChannel(CHANNEL, "Capture", NotificationManager.IMPORTANCE_LOW))
        val flags = PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT
        val open = PendingIntent.getActivity(this, 0, Intent(this, MainActivity::class.java), flags)
        val stop = PendingIntent.getService(this, 1, Intent(this, PhoneRecorder::class.java).setAction(ACTION_STOP), flags)
        val n = Notification.Builder(this, CHANNEL).setSmallIcon(android.R.drawable.ic_btn_speak_now)
            .setContentTitle("Capturing").setContentText("MindCapture is listening. Tap Stop to finish.")
            .setOngoing(true).setUsesChronometer(true).setWhen(System.currentTimeMillis()).setContentIntent(open)
            .addAction(Notification.Action.Builder(null, "Stop", stop).build()).build()
        ServiceCompat.startForeground(this, 1, n, if (Build.VERSION.SDK_INT >= 30) ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE else 0)

        Files.audio(this, 0).parentFile?.listFiles { f -> f.extension == "part" }?.forEach { it.delete() }  // crashed sessions are unplayable
        if (!beginSegment()) { stopForeground(STOP_FOREGROUND_REMOVE); stopSelf(); return }
        _state.value = RecState(true, System.currentTimeMillis())
        handler.post(sample)
    }

    /** id = segment start time, same convention as watch recordings. */
    private fun beginSegment(): Boolean {
        val id = System.currentTimeMillis()
        val out = File(Files.audio(this, id).parentFile, "$id.part")
        val r = if (Build.VERSION.SDK_INT >= 31) MediaRecorder(this) else @Suppress("DEPRECATION") MediaRecorder()
        try {
            r.setAudioSource(MediaRecorder.AudioSource.MIC)
            r.setOutputFormat(MediaRecorder.OutputFormat.MPEG_4)
            r.setAudioEncoder(MediaRecorder.AudioEncoder.AAC)
            r.setAudioChannels(1)
            r.setAudioSamplingRate(16000)
            r.setAudioEncodingBitRate(32000)  // speech; a 30 min segment is ~7 MB, under the STT upload limit
            r.setOutputFile(out.absolutePath)
            r.prepare()
            r.start()
        } catch (e: Exception) {
            r.release(); out.delete(); return false
        }
        rec = r
        part = out
        // ponytail: long sessions become one capture per 30 min segment; merge server-side if one capture per session matters
        handler.postDelayed(roll, SEGMENT_MS)
        return true
    }

    private fun finishSegment() {
        handler.removeCallbacks(roll)
        val r = rec ?: return
        rec = null
        val out = part ?: return
        val ok = try { r.stop(); true } catch (e: RuntimeException) { false }  // stop() throws if nothing was recorded
        r.release()
        val id = out.nameWithoutExtension.toLong()
        if (!ok || !out.renameTo(Files.audio(this, id))) { out.delete(); return }
        runBlocking(Dispatchers.IO) {
            if (Db.get(this@PhoneRecorder).dao().insertCapture(Capture(id, id, "queued")) != -1L) ProcessWorker.enqueue(this@PhoneRecorder, id)
        }
    }

    private fun stop() {
        handler.removeCallbacks(sample)
        finishSegment()
        _state.value = RecState()
        stopForeground(STOP_FOREGROUND_REMOVE)
        stopSelf()
    }

    override fun onDestroy() {
        if (rec != null) stop()  // never lose a session to a service kill
        super.onDestroy()
    }

    companion object {
        private const val ACTION_STOP = "stop"
        private const val CHANNEL = "capture"
        private const val SEGMENT_MS = 30 * 60 * 1000L
        const val LEVELS = 48

        private val _state = MutableStateFlow(RecState())
        val state: StateFlow<RecState> = _state

        fun start(ctx: Context) = ContextCompat.startForegroundService(ctx, Intent(ctx, PhoneRecorder::class.java))
        fun stop(ctx: Context) { ctx.startService(Intent(ctx, PhoneRecorder::class.java).setAction(ACTION_STOP)) }
    }
}
