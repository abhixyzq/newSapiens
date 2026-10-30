package com.newsapiens.memory.mobile

import android.Manifest
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.BackHandler
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import java.time.Instant
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import java.time.format.FormatStyle
import kotlinx.coroutines.launch

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent { MaterialTheme { App() } }
    }
}

private val fmt = DateTimeFormatter.ofLocalizedDateTime(FormatStyle.MEDIUM, FormatStyle.SHORT)
private fun whenText(ms: Long) = fmt.format(Instant.ofEpochMilli(ms).atZone(ZoneId.systemDefault()))

@Composable
private fun App() {
    var open by rememberSaveable { mutableStateOf<Long?>(null) }
    var settings by rememberSaveable { mutableStateOf(false) }
    BackHandler(open != null || settings) { open = null; settings = false }
    when {
        settings -> SettingsScreen { settings = false }
        open != null -> DetailScreen(open!!) { open = null }
        else -> ListScreen(onOpen = { open = it }, onSettings = { settings = true })
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun ListScreen(onOpen: (Long) -> Unit, onSettings: () -> Unit) {
    val ctx = LocalContext.current
    val dao = remember { Db.get(ctx).dao() }
    val all by dao.captures().collectAsState(emptyList())
    var query by rememberSaveable { mutableStateOf("") }
    var hits by remember { mutableStateOf<List<Capture>?>(null) }
    LaunchedEffect(query) { hits = ftsQuery(query)?.let { dao.search(it) } }
    val shown = hits ?: all
    Scaffold(topBar = { TopAppBar(title = { Text("Memory") }, actions = { TextButton(onSettings) { Text("Settings") } }) }) { pad ->
        Column(Modifier.padding(pad).padding(horizontal = 16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            OutlinedTextField(query, { query = it }, Modifier.fillMaxWidth(), label = { Text("Search captures") }, singleLine = true)
            if (shown.isEmpty()) Text(if (query.isBlank()) "Record on your watch — captures appear here." else "Nothing found.")
            LazyColumn(verticalArrangement = Arrangement.spacedBy(8.dp), contentPadding = PaddingValues(bottom = 16.dp)) {
                items(shown, key = { it.id }) { c ->
                    Card(Modifier.fillMaxWidth().clickable { onOpen(c.id) }) {
                        Column(Modifier.padding(12.dp)) {
                            Text(whenText(c.startedAt), style = MaterialTheme.typography.labelMedium)
                            Text(
                                if (c.status == "ready") c.summary.ifBlank { "(no summary)" } else statusText(c),
                                maxLines = 3, style = MaterialTheme.typography.bodyMedium,
                            )
                        }
                    }
                }
            }
        }
    }
}

private fun statusText(c: Capture) = when (c.status) {
    "queued" -> "Waiting to upload" + (c.error?.let { " — $it" } ?: "")
    "processing" -> "Transcribing and summarising…"
    else -> "Failed — ${c.error ?: "unknown error"}"
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun DetailScreen(id: Long, onBack: () -> Unit) {
    val ctx = LocalContext.current
    val dao = remember { Db.get(ctx).dao() }
    val scope = rememberCoroutineScope()
    val cap by dao.captureFlow(id).collectAsState(null)
    val decisions by dao.decisions(id).collectAsState(emptyList())
    val tasks by dao.tasks(id).collectAsState(emptyList())
    var msg by remember { mutableStateOf<String?>(null) }

    fun addToCalendar() = scope.launch {
        val n = CalendarSync.addDue(ctx, dao, id)
        msg = when {
            n < 0 -> "No writable calendar on this phone — add a Google account first"
            n == 0 -> "No dated tasks left to add"
            else -> "Added $n event(s) to your calendar"
        }
    }
    val perms = rememberLauncherForActivityResult(ActivityResultContracts.RequestMultiplePermissions()) { granted ->
        if (granted.values.all { it }) addToCalendar() else msg = "Calendar permission denied"
    }

    val c = cap
    Scaffold(topBar = { TopAppBar(title = { Text(c?.let { whenText(it.startedAt) } ?: "") }, navigationIcon = { TextButton(onBack) { Text("Back") } }) }) { pad ->
        if (c == null) { Text("Capture deleted.", Modifier.padding(pad).padding(16.dp)); return@Scaffold }
        Column(Modifier.padding(pad).padding(16.dp).verticalScroll(rememberScrollState()), verticalArrangement = Arrangement.spacedBy(12.dp)) {
            if (c.status != "ready") {
                Text(statusText(c))
                if (c.status == "failed") Button({ scope.launch { dao.updateCapture(c.copy(status = "queued", error = null)); ProcessWorker.enqueue(ctx, id) } }) { Text("Retry") }
            } else {
                Text(c.summary, style = MaterialTheme.typography.bodyLarge)
                if (c.topics.isNotBlank()) Text("Topics: ${c.topics}", style = MaterialTheme.typography.labelMedium)
                if (decisions.isNotEmpty()) {
                    Text("Decisions", style = MaterialTheme.typography.titleMedium)
                    decisions.forEach { Text("• ${it.text}") }
                }
                if (tasks.isNotEmpty()) {
                    Text("Action items", style = MaterialTheme.typography.titleMedium)
                    tasks.forEach { t ->
                        val due = t.dueDate ?: t.dueText?.let { "“$it”" }
                        Text("• ${t.title}" + (t.assignee?.let { " ($it)" } ?: "") + (due?.let { " — $it" } ?: "") + if (t.calendarEventId != null) "  ✓ calendar" else "")
                    }
                    Text("Extracted by AI — check before relying on it.", style = MaterialTheme.typography.labelSmall)
                    if (tasks.any { it.dueDate != null && it.calendarEventId == null }) {
                        OutlinedButton({
                            if (CalendarSync.hasPermission(ctx)) addToCalendar()
                            else perms.launch(arrayOf(Manifest.permission.READ_CALENDAR, Manifest.permission.WRITE_CALENDAR))
                        }) { Text("Add dated tasks to calendar") }
                    }
                }
                msg?.let { Text(it, style = MaterialTheme.typography.labelMedium) }
                Text("Transcript", style = MaterialTheme.typography.titleMedium)
                Text(c.transcript.ifBlank { "(empty)" }, style = MaterialTheme.typography.bodySmall)
            }
            OutlinedButton({
                scope.launch { Files.audio(ctx, id).delete(); dao.deleteCapture(id); onBack() }
            }) { Text("Delete capture") }
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun SettingsScreen(onBack: () -> Unit) {
    val ctx = LocalContext.current
    var url by remember { mutableStateOf(Prefs.baseUrl(ctx)) }
    var token by remember { mutableStateOf(Prefs.token(ctx)) }
    Scaffold(topBar = { TopAppBar(title = { Text("Settings") }, navigationIcon = { TextButton(onBack) { Text("Back") } }) }) { pad ->
        Column(Modifier.padding(pad).padding(16.dp).fillMaxSize(), verticalArrangement = Arrangement.spacedBy(12.dp)) {
            OutlinedTextField(url, { url = it }, Modifier.fillMaxWidth(), label = { Text("Backend URL (https://…)") }, singleLine = true)
            OutlinedTextField(token, { token = it }, Modifier.fillMaxWidth(), label = { Text("API token") }, singleLine = true)
            Row { Button({ Prefs.save(ctx, url, token); onBack() }) { Text("Save") } }
            Text("Audio is uploaded for transcription and deleted from the phone as soon as the result is saved.", style = MaterialTheme.typography.labelMedium)
        }
    }
}
