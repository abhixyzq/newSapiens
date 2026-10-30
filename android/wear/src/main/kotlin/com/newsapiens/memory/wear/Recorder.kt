package com.newsapiens.memory.wear

import android.content.Context
import java.io.File
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow

data class RecState(val recording: Boolean = false, val startedAt: Long = 0)

object Recorder {
    /** Default session cap; the watch battery, not the backend, is the limit. */
    const val MAX_SESSION_MS = 60 * 60 * 1000L
    const val PATH_RECORDING = "/recording/"
    const val PATH_ACK = "/ack"

    private val _state = MutableStateFlow(RecState())
    val state: StateFlow<RecState> = _state

    fun set(s: RecState) { _state.value = s }

    fun dir(ctx: Context) = File(ctx.filesDir, "recordings").apply { mkdirs() }

    /** Finished recordings are `<startEpochMs>.m4a`; the one being written is `.part` and is never sent. */
    fun ready(ctx: Context): List<File> = dir(ctx).listFiles { f -> f.extension == "m4a" }?.sortedBy { it.name }.orEmpty()
}
