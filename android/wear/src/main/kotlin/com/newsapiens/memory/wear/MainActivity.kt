package com.newsapiens.memory.wear

import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.size
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableLongStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import androidx.core.content.ContextCompat
import androidx.wear.compose.material.Button
import androidx.wear.compose.material.MaterialTheme
import androidx.wear.compose.material.Text
import kotlinx.coroutines.delay

class MainActivity : ComponentActivity() {
    private var wantToggle = false

    private val mic = registerForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
        if (granted && wantToggle) RecorderService.toggle(this)
        wantToggle = false
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        SendWorker.enqueue(this)  // flush anything left over (phone was away, ack lost, ...)
        handle(intent)
        setContent { MaterialTheme { Screen(onToggle = ::toggle) } }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        handle(intent)
    }

    /** The tile can only launch an activity, so it opens us with toggle=true; a foreground activity may start the mic service. */
    private fun handle(i: Intent) {
        if (i.getBooleanExtra("toggle", false)) { i.removeExtra("toggle"); toggle() }
    }

    private fun toggle() {
        if (ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED) {
            RecorderService.toggle(this)
        } else {
            wantToggle = true
            mic.launch(Manifest.permission.RECORD_AUDIO)
        }
    }
}

@Composable
private fun Screen(onToggle: () -> Unit) {
    val s by Recorder.state.collectAsState()
    var now by remember { mutableLongStateOf(System.currentTimeMillis()) }
    LaunchedEffect(s.recording) { while (s.recording) { now = System.currentTimeMillis(); delay(1000) } }
    val secs = if (s.recording) ((now - s.startedAt) / 1000).coerceAtLeast(0) else 0
    Column(Modifier.fillMaxSize(), verticalArrangement = Arrangement.Center, horizontalAlignment = Alignment.CenterHorizontally) {
        Text(if (s.recording) "%d:%02d".format(secs / 60, secs % 60) else "Ready", style = MaterialTheme.typography.display3)
        Button(onClick = onToggle, modifier = Modifier.size(64.dp)) { Text(if (s.recording) "Stop" else "Rec") }
    }
}
