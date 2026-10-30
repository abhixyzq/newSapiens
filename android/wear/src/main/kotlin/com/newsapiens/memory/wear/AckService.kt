package com.newsapiens.memory.wear

import com.google.android.gms.wearable.MessageEvent
import com.google.android.gms.wearable.WearableListenerService
import java.io.File

class AckService : WearableListenerService() {
    override fun onMessageReceived(ev: MessageEvent) {
        if (ev.path != Recorder.PATH_ACK) return
        File(Recorder.dir(this), File(String(ev.data)).name).delete()  // .name: never trust a path from the wire
    }
}
