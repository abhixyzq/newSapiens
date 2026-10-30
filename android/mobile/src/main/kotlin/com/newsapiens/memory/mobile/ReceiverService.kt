package com.newsapiens.memory.mobile

import android.net.Uri
import com.google.android.gms.wearable.ChannelClient
import com.google.android.gms.wearable.Wearable
import com.google.android.gms.wearable.WearableListenerService
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.tasks.await

/** Receives recordings from the watch. The watch deletes its copy only after the ack sent here, so the
 *  capture row + audio file are persisted first. Callbacks run on a background thread, hence runBlocking. */
class ReceiverService : WearableListenerService() {
    private fun idOf(channel: ChannelClient.Channel) = channel.path.substringAfterLast('/').substringBefore('.').toLongOrNull()

    override fun onChannelOpened(channel: ChannelClient.Channel) {
        val id = idOf(channel) ?: return
        Wearable.getChannelClient(this).receiveFile(channel, Uri.fromFile(Files.audio(this, id)), false)
    }

    override fun onInputClosed(channel: ChannelClient.Channel, closeReason: Int, appSpecificErrorCode: Int) {
        val id = idOf(channel) ?: return
        val file = Files.audio(this, id)
        if (closeReason != ChannelClient.ChannelCallback.CLOSE_REASON_NORMAL) { file.delete(); return }  // watch will resend
        runBlocking {
            val dao = Db.get(this@ReceiverService).dao()
            if (dao.insertCapture(Capture(id, id, "queued")) == -1L) file.delete()  // duplicate resend (ack was lost)
            else ProcessWorker.enqueue(this@ReceiverService, id)
            Wearable.getMessageClient(this@ReceiverService).sendMessage(channel.nodeId, "/ack", "$id.m4a".toByteArray()).await()
        }
    }
}
