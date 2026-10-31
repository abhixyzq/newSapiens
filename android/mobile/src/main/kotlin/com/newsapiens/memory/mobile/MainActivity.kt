package com.newsapiens.memory.mobile

import android.Manifest
import android.os.Build
import android.os.Bundle
import android.widget.Toast
import androidx.activity.ComponentActivity
import androidx.activity.compose.BackHandler
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.animation.animateContentSize
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.rounded.ArrowBack
import androidx.compose.material.icons.automirrored.rounded.ArrowForwardIos
import androidx.compose.material.icons.rounded.AutoAwesome
import androidx.compose.material.icons.rounded.ExpandLess
import androidx.compose.material.icons.rounded.ExpandMore
import androidx.compose.material.icons.rounded.Home
import androidx.compose.material.icons.rounded.Mic
import androidx.compose.material.icons.rounded.Search
import androidx.compose.material.icons.rounded.Settings
import androidx.compose.material.icons.rounded.Stop
import androidx.compose.material.icons.rounded.TaskAlt
import androidx.compose.material.icons.rounded.Timeline
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.CenterAlignedTopAppBar
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.NavigationBar
import androidx.compose.material3.NavigationBarItem
import androidx.compose.material3.NavigationBarItemDefaults
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableLongStateOf
import androidx.compose.runtime.mutableStateListOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import java.time.LocalDate
import java.time.LocalTime
import java.time.format.DateTimeFormatter
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        setContent { AppTheme { App() } }
    }
}

enum class Tab(val label: String, val icon: ImageVector) {
    Home("Home", Icons.Rounded.Home), Timeline("Timeline", Icons.Rounded.Timeline), Tasks("Tasks", Icons.Rounded.TaskAlt),
    Search("Search", Icons.Rounded.Search), Settings("Settings", Icons.Rounded.Settings),
}

@Composable
private fun App() {
    var tab by rememberSaveable { mutableStateOf(Tab.Home) }
    var open by rememberSaveable { mutableStateOf<Long?>(null) }
    var openLine by rememberSaveable { mutableStateOf<Int?>(null) }  // transcript line to show when a moment/event/task is opened
    var insights by rememberSaveable { mutableStateOf(false) }
    val openAt = { id: Long, line: Int? -> open = id; openLine = line }
    var capture by rememberSaveable { mutableStateOf(PhoneRecorder.state.value.recording) }  // reopening mid-capture lands on the timer
    var asking by rememberSaveable { mutableStateOf(false) }
    val chat = remember { mutableStateListOf<AskMsg>() }  // ponytail: kept in memory only; persist in Room if people want old chats
    BackHandler(open != null || capture || asking || insights || tab != Tab.Home) {
        when { open != null -> open = null; capture -> capture = false; asking -> asking = false; insights -> insights = false; else -> tab = Tab.Home }
    }
    when {
        open != null -> DetailScreen(open!!, openLine) { open = null }
        insights -> Scaffold(containerColor = MaterialTheme.colorScheme.background) { pad -> Box(Modifier.padding(pad)) { InsightsScreen { insights = false } } }
        asking -> AskScreen(chat) { asking = false }
        capture -> CaptureScreen { capture = false }
        else -> Scaffold(
            containerColor = MaterialTheme.colorScheme.background,
            bottomBar = {
                NavigationBar(containerColor = MaterialTheme.colorScheme.surface, tonalElevation = 0.dp) {
                    Tab.entries.forEach { t ->
                        NavigationBarItem(
                            selected = t == tab, onClick = { tab = t },
                            icon = { Icon(t.icon, null) }, label = { Text(t.label) },
                            colors = NavigationBarItemDefaults.colors(
                                selectedIconColor = MaterialTheme.colorScheme.onBackground,
                                selectedTextColor = MaterialTheme.colorScheme.onBackground,
                                indicatorColor = MaterialTheme.colorScheme.primaryContainer,
                            ),
                        )
                    }
                }
            },
        ) { pad ->
            Box(Modifier.padding(pad)) {
                when (tab) {
                    Tab.Home -> HomeScreen(onOpen = openAt, onCapture = { capture = true }, onAsk = { asking = true }, onTab = { tab = it }, onInsights = { insights = true })
                    Tab.Timeline -> TimelineScreen(openAt)
                    Tab.Tasks -> TasksScreen(openAt)
                    Tab.Search -> MemoryScreen { open = it; openLine = null }
                    Tab.Settings -> ProfileScreen()
                }
            }
        }
    }
}

/** Asks for the mic (and, on 13+, notifications so the Stop control is visible), then starts the background recorder. */
@Composable
private fun rememberStartCapture(onStarted: () -> Unit): () -> Unit {
    val ctx = LocalContext.current
    val perms = if (Build.VERSION.SDK_INT >= 33) arrayOf(Manifest.permission.RECORD_AUDIO, Manifest.permission.POST_NOTIFICATIONS)
    else arrayOf(Manifest.permission.RECORD_AUDIO)
    val launcher = rememberLauncherForActivityResult(ActivityResultContracts.RequestMultiplePermissions()) { granted ->
        if (granted[Manifest.permission.RECORD_AUDIO] == true) { PhoneRecorder.start(ctx); onStarted() }
        else Toast.makeText(ctx, "Microphone permission is needed to capture", Toast.LENGTH_LONG).show()
    }
    return { launcher.launch(perms) }
}

private fun greeting() = when (LocalTime.now().hour) { in 5..11 -> "Good morning"; in 12..16 -> "Good afternoon"; else -> "Good evening" }

@Composable
private fun HomeScreen(onOpen: (Long, Int?) -> Unit, onCapture: () -> Unit, onAsk: () -> Unit, onTab: (Tab) -> Unit, onInsights: () -> Unit) {
    val ctx = LocalContext.current
    val dao = remember { Db.get(ctx).dao() }
    val scope = rememberCoroutineScope()
    val today = LocalDate.now()
    val (from, to) = remember(today) { dayRange(today) }
    val caps by remember(from) { dao.capturesBetween(from, to) }.collectAsState(emptyList())
    val moments by remember(from) { dao.momentsBetween(from, to) }.collectAsState(emptyList())
    val summary by remember(today) { dao.summary(today.toString()) }.collectAsState(null)
    val recent by dao.captures().collectAsState(emptyList())
    val tasks by dao.allTasks().collectAsState(emptyList())
    val rec by PhoneRecorder.state.collectAsState()
    val name = remember { Prefs.name(ctx) }
    val start = rememberStartCapture(onCapture)
    var expanded by rememberSaveable { mutableStateOf(false) }
    val cs = MaterialTheme.colorScheme

    val key = moments.filter { it.importance >= 0.5 }
    val (dueNow, upcoming, unconfirmed) = taskBuckets(tasks, today)
    Box(Modifier.fillMaxSize()) {
        Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(horizontal = 20.dp)) {
            Spacer(Modifier.height(20.dp))
            Row(verticalAlignment = Alignment.CenterVertically) {
                Column(Modifier.weight(1f)) {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Text(today.format(DateTimeFormatter.ofPattern("EEEE, MMMM d")), style = MaterialTheme.typography.labelLarge, color = cs.onSurfaceVariant)
                        if (rec.recording) {
                            Spacer(Modifier.width(10.dp))
                            Box(Modifier.size(7.dp).clip(CircleShape).background(cs.error))
                            Spacer(Modifier.width(5.dp))
                            Text("Listening", style = MaterialTheme.typography.labelMedium, color = cs.error)
                        }
                    }
                    Text(if (name.isBlank()) "${greeting()}!" else "${greeting()}, $name", style = MaterialTheme.typography.headlineSmall, fontWeight = FontWeight.Bold)
                }
                Box(Modifier.size(44.dp).clip(CircleShape).background(cs.primary).clickable { onTab(Tab.Settings) }, contentAlignment = Alignment.Center) {
                    Text(name.firstOrNull()?.uppercase() ?: "M", color = cs.onPrimary, fontWeight = FontWeight.Bold, fontSize = 18.sp)
                }
            }
            Spacer(Modifier.height(14.dp))

            Row(
                Modifier.fillMaxWidth().clip(CircleShape).background(cs.surface).border(BorderStroke(1.dp, cs.outlineVariant), CircleShape)
                    .clickable(onClick = onAsk).padding(horizontal = 18.dp, vertical = 13.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Icon(Icons.Rounded.AutoAwesome, null, Modifier.size(20.dp), tint = cs.primary)
                Spacer(Modifier.width(10.dp))
                Text("Ask your memory…", color = cs.onSurfaceVariant, style = MaterialTheme.typography.bodyLarge)
            }
            Spacer(Modifier.height(14.dp))

            // Today's overview: all from stored data; tap for the longer-range Insights
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                Stat(spokenText(caps.sumOf { it.speechS ?: 0.0 }), "Speech", Modifier.weight(1f), onInsights)
                Stat("${sessions(caps)}", "Sessions", Modifier.weight(1f), onInsights)
                Stat("${key.size}", "Moments", Modifier.weight(1f)) { onTab(Tab.Timeline) }
                Stat("${tasks.count { !it.done }}", "Pending", Modifier.weight(1f)) { onTab(Tab.Tasks) }
            }
            Spacer(Modifier.height(14.dp))

            // Your day at a glance
            Column(
                Modifier.fillMaxWidth().clip(RoundedCornerShape(20.dp)).background(cs.surface).border(BorderStroke(1.dp, cs.outlineVariant), RoundedCornerShape(20.dp))
                    .clickable { expanded = !expanded }.animateContentSize().padding(16.dp),
            ) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Icon(Icons.Rounded.AutoAwesome, null, Modifier.size(18.dp), tint = cs.primary)
                    Spacer(Modifier.width(8.dp))
                    Text("Your day at a glance", style = MaterialTheme.typography.titleSmall, fontWeight = FontWeight.Bold, modifier = Modifier.weight(1f))
                    if (summary != null) Icon(if (expanded) Icons.Rounded.ExpandLess else Icons.Rounded.ExpandMore, null, tint = cs.onSurfaceVariant)
                }
                Spacer(Modifier.height(8.dp))
                val s = summary
                val highlights = s?.highlights?.lines()?.filter { it.isNotBlank() }.orEmpty()
                Text(
                    when {
                        s != null && s.summary.isNotBlank() -> s.summary
                        s != null && highlights.isNotEmpty() -> "Main events: " + highlights.joinToString("; ") + "."
                        caps.isEmpty() -> "Nothing captured yet today. Tap Capture to start."
                        else -> "Your summary appears a couple of minutes after today's captures are processed."
                    },
                    style = MaterialTheme.typography.bodyMedium, lineHeight = 21.sp,
                    color = if (s == null) cs.onSurfaceVariant else cs.onSurface,
                )
                if (expanded && s != null) {
                    if (s.summary.isNotBlank()) highlights.forEach { Text("•  $it", style = MaterialTheme.typography.bodySmall, modifier = Modifier.padding(top = 6.dp)) }
                    Text("Updated ${timeText(s.generatedAt)} from today's transcripts · AI-written; “Likely” marks inferences",
                        style = MaterialTheme.typography.labelSmall, color = cs.onSurfaceVariant, modifier = Modifier.padding(top = 8.dp))
                }
            }
            Spacer(Modifier.height(20.dp))

            Row(verticalAlignment = Alignment.CenterVertically) {
                SectionTitle("Important moments", Modifier.weight(1f))
                TextButton({ onTab(Tab.Timeline) }) { Text("Timeline") }
            }
            Column(Modifier.animateContentSize()) {
                if (key.isEmpty()) Text("Decisions, tasks, commitments and ideas from today show up here at the time they were said.",
                    style = MaterialTheme.typography.bodyMedium, color = cs.onSurfaceVariant, modifier = Modifier.padding(vertical = 6.dp))
                key.takeLast(6).forEach { m -> MomentRow(m) { onOpen(m.captureId, m.line) } }
            }
            Spacer(Modifier.height(16.dp))

            Row(verticalAlignment = Alignment.CenterVertically) {
                SectionTitle("What to do", Modifier.weight(1f))
                TextButton({ onTab(Tab.Tasks) }) { Text("All tasks") }
            }
            if (dueNow.isEmpty() && unconfirmed.isEmpty()) Text("Nothing open.", color = cs.onSurfaceVariant, style = MaterialTheme.typography.bodyMedium)
            (unconfirmed + dueNow).take(3).forEach { t -> TaskRow(t, onClick = { onTab(Tab.Tasks) }) { scope.launch { dao.setDone(t.id, it) } } }
            if (upcoming.isNotEmpty()) Text("Upcoming: " + upcoming.take(2).joinToString(" · ") { "${it.title} (${dueText(it.dueDate!!)})" },
                style = MaterialTheme.typography.bodySmall, color = cs.onSurfaceVariant, modifier = Modifier.padding(top = 4.dp).clickable { onTab(Tab.Tasks) })
            Spacer(Modifier.height(16.dp))

            Row(verticalAlignment = Alignment.CenterVertically) {
                SectionTitle("Recent captures", Modifier.weight(1f))
                TextButton({ onTab(Tab.Search) }) { Text("View all") }
            }
            if (recent.isEmpty()) Text("Tap Capture below or record on your watch — captures appear here.",
                style = MaterialTheme.typography.bodyMedium, color = cs.onSurfaceVariant, modifier = Modifier.padding(vertical = 8.dp))
            recent.take(3).forEach { CaptureRow(it, shortWhen(it)) { onOpen(it.id, null) } }
            Spacer(Modifier.height(100.dp))  // room for the floating Capture pill
        }

        Button(
            onClick = { if (rec.recording) onCapture() else start() },
            modifier = Modifier.align(Alignment.BottomCenter).padding(bottom = 16.dp).height(56.dp),
            shape = CircleShape,
            colors = ButtonDefaults.buttonColors(containerColor = cs.onBackground, contentColor = cs.background),
            contentPadding = PaddingValues(horizontal = 28.dp),
            elevation = ButtonDefaults.buttonElevation(defaultElevation = 6.dp),
        ) {
            if (rec.recording) {
                Box(Modifier.size(10.dp).clip(CircleShape).background(cs.error))
                Spacer(Modifier.width(10.dp))
                Elapsed(rec.startedAt) { Text("Recording · $it", fontWeight = FontWeight.SemiBold) }
            } else {
                Icon(Icons.Rounded.Mic, null)
                Spacer(Modifier.width(8.dp))
                Text("Capture", fontWeight = FontWeight.SemiBold, fontSize = 16.sp)
            }
        }
    }
}

@Composable
private fun Stat(value: String, label: String, modifier: Modifier, onClick: () -> Unit) {
    val cs = MaterialTheme.colorScheme
    Column(
        modifier.clip(RoundedCornerShape(16.dp)).background(cs.surface).border(BorderStroke(1.dp, cs.outlineVariant), RoundedCornerShape(16.dp))
            .clickable(onClick = onClick).padding(vertical = 12.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        Text(value, style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.Bold, maxLines = 1)
        Text(label, style = MaterialTheme.typography.labelSmall, color = cs.onSurfaceVariant)
    }
}

/** Ticks twice a second while [startedAt] is set and renders the elapsed clock. */
@Composable
private fun Elapsed(startedAt: Long, content: @Composable (String) -> Unit) {
    var now by remember { mutableLongStateOf(System.currentTimeMillis()) }
    LaunchedEffect(startedAt) { while (startedAt != 0L) { now = System.currentTimeMillis(); delay(500) } }
    content(clock(if (startedAt == 0L) 0 else (now - startedAt).coerceAtLeast(0)))
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun CaptureScreen(onBack: () -> Unit) {
    val ctx = LocalContext.current
    val s by PhoneRecorder.state.collectAsState()
    var stopped by remember { mutableStateOf(false) }
    val start = rememberStartCapture { stopped = false }
    val cs = MaterialTheme.colorScheme
    Scaffold(
        containerColor = cs.background,
        topBar = {
            CenterAlignedTopAppBar(
                title = { Text("Capture", fontWeight = FontWeight.SemiBold) },
                navigationIcon = { IconButton(onBack) { Icon(Icons.AutoMirrored.Rounded.ArrowBack, "Back") } },
                colors = TopAppBarDefaults.centerAlignedTopAppBarColors(containerColor = cs.background),
            )
        },
    ) { pad ->
        Column(Modifier.padding(pad).fillMaxSize().padding(24.dp), horizontalAlignment = Alignment.CenterHorizontally) {
            Row(
                Modifier.clip(CircleShape).background(if (s.recording) cs.error.copy(alpha = 0.12f) else cs.surfaceVariant).padding(horizontal = 16.dp, vertical = 8.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                if (s.recording) { Box(Modifier.size(10.dp).clip(CircleShape).background(cs.error)); Spacer(Modifier.width(8.dp)) }
                Text(
                    when { s.recording -> "Recording…"; stopped -> "Saved — processing in the background"; else -> "Ready" },
                    style = MaterialTheme.typography.labelLarge, color = if (s.recording) cs.error else cs.onSurfaceVariant,
                )
            }
            Spacer(Modifier.height(48.dp))
            Elapsed(s.startedAt) { Text(it, fontSize = 64.sp, fontWeight = FontWeight.SemiBold, letterSpacing = 1.sp) }
            Spacer(Modifier.height(40.dp))
            Waveform(s.levels, Modifier.fillMaxWidth().height(120.dp))
            Spacer(Modifier.weight(1f))
            Box(
                Modifier.size(84.dp).clip(CircleShape).background(if (s.recording) cs.onBackground else cs.error)
                    .clickable { if (s.recording) { PhoneRecorder.stop(ctx); stopped = true } else start() },
                contentAlignment = Alignment.Center,
            ) {
                Icon(if (s.recording) Icons.Rounded.Stop else Icons.Rounded.Mic, if (s.recording) "Stop" else "Start",
                    Modifier.size(38.dp), tint = if (s.recording) cs.background else cs.onPrimary)
            }
            Spacer(Modifier.height(20.dp))
            Text(
                if (s.recording) "Keeps recording with the app closed. Stop here or from the notification."
                else "Captures are transcribed, summarised and added to Memory.",
                style = MaterialTheme.typography.bodySmall, color = cs.onSurfaceVariant, textAlign = TextAlign.Center,
            )
            Spacer(Modifier.height(12.dp))
        }
    }
}

@Composable
private fun Waveform(levels: List<Float>, modifier: Modifier) {
    val color = MaterialTheme.colorScheme.primary
    val faint = MaterialTheme.colorScheme.outlineVariant
    Canvas(modifier) {
        val n = PhoneRecorder.LEVELS
        val step = size.width / n
        val padded = List(n - levels.size) { 0f } + levels
        padded.forEachIndexed { i, l ->
            val h = (0.06f + l * 0.94f).coerceAtMost(1f) * size.height
            val x = step * i + step / 2
            drawLine(if (l > 0f) color else faint, Offset(x, (size.height - h) / 2), Offset(x, (size.height + h) / 2),
                strokeWidth = step * 0.55f, cap = StrokeCap.Round)
        }
    }
}
