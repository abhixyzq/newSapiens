package com.newsapiens.memory.wear

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.media.MediaRecorder
import android.os.Build
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import androidx.wear.tiles.TileService
import java.io.File

class RecorderService : Service() {
    private var rec: MediaRecorder? = null
    private var part: File? = null
    private var startedAt = 0L
    private val handler = Handler(Looper.getMainLooper())
    private val cap = Runnable { stopRecording() }

    override fun onBind(i: Intent?): IBinder? = null

    override fun onStartCommand(i: Intent?, flags: Int, startId: Int): Int {
        if (i?.action == ACTION_STOP) stopRecording() else startRecording()
        return START_NOT_STICKY
    }

    private fun startRecording() {
        if (rec != null) return
        val nm = getSystemService(NotificationManager::class.java)
        nm.createNotificationChannel(NotificationChannel(CHANNEL, "Recording", NotificationManager.IMPORTANCE_LOW))
        val n = Notification.Builder(this, CHANNEL).setSmallIcon(android.R.drawable.ic_btn_speak_now)
            .setContentTitle("Recording").setOngoing(true).build()
        startForeground(1, n, ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE)

        Recorder.dir(this).listFiles { f -> f.extension == "part" }?.forEach { it.delete() }  // crashed sessions are unplayable
        startedAt = System.currentTimeMillis()
        val out = File(Recorder.dir(this), "$startedAt.part")
        val r = if (Build.VERSION.SDK_INT >= 31) MediaRecorder(this) else @Suppress("DEPRECATION") MediaRecorder()
        try {
            r.setAudioSource(MediaRecorder.AudioSource.MIC)
            r.setOutputFormat(MediaRecorder.OutputFormat.MPEG_4)
            r.setAudioEncoder(MediaRecorder.AudioEncoder.AAC)
            r.setAudioChannels(1)
            r.setAudioSamplingRate(16000)  // speech; keeps a 1h session near 30 MB
            r.setAudioEncodingBitRate(64000)
            r.setOutputFile(out.absolutePath)
            r.prepare()
            r.start()
        } catch (e: Exception) {
            r.release()
            out.delete()
            stopForeground(STOP_FOREGROUND_REMOVE)
            stopSelf()
            return
        }
        rec = r
        part = out
        Recorder.set(RecState(true, startedAt))
        handler.postDelayed(cap, Recorder.MAX_SESSION_MS)
        TileService.getUpdater(this).requestUpdate(RecordingTile::class.java)
    }

    private fun stopRecording() {
        handler.removeCallbacks(cap)
        val r = rec ?: run { stopSelf(); return }
        rec = null
        val out = part
        val ok = try { r.stop(); true } catch (e: RuntimeException) { false }  // stop() throws if nothing was recorded
        r.release()
        if (out != null) {
            if (ok) out.renameTo(File(out.parentFile, "${out.nameWithoutExtension}.m4a")) else out.delete()
        }
        Recorder.set(RecState())
        TileService.getUpdater(this).requestUpdate(RecordingTile::class.java)
        stopForeground(STOP_FOREGROUND_REMOVE)
        stopSelf()
        SendWorker.enqueue(this)
    }

    override fun onDestroy() {
        if (rec != null) stopRecording()  // never lose a session to a service kill
        super.onDestroy()
    }

    companion object {
        const val ACTION_STOP = "stop"
        private const val CHANNEL = "recording"

        fun toggle(ctx: Context) {
            val i = Intent(ctx, RecorderService::class.java)
            if (Recorder.state.value.recording) ctx.startService(i.setAction(ACTION_STOP)) else ctx.startForegroundService(i)
        }
    }
}
