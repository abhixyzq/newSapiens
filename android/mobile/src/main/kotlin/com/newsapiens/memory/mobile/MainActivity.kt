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
import androidx.compose.material.icons.rounded.CheckCircle
import androidx.compose.material.icons.rounded.Description
import androidx.compose.material.icons.rounded.Home
import androidx.compose.material.icons.rounded.Insights
import androidx.compose.material.icons.rounded.Layers
import androidx.compose.material.icons.rounded.Mic
import androidx.compose.material.icons.rounded.Person
import androidx.compose.material.icons.rounded.Stop
import androidx.compose.material.icons.rounded.TaskAlt
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
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import java.time.LocalTime
import kotlinx.coroutines.delay

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        setContent { AppTheme { App() } }
    }
}

enum class Tab(val label: String, val icon: ImageVector) {
    Home("Home", Icons.Rounded.Home), Memory("Memory", Icons.Rounded.Layers), Insights("Insights", Icons.Rounded.Insights),
    Actions("Actions", Icons.Rounded.TaskAlt), Profile("Profile", Icons.Rounded.Person),
}

@Composable
private fun App() {
    var tab by rememberSaveable { mutableStateOf(Tab.Home) }
    var open by rememberSaveable { mutableStateOf<Long?>(null) }
    var capture by rememberSaveable { mutableStateOf(PhoneRecorder.state.value.recording) }  // reopening mid-capture lands on the timer
    var asking by rememberSaveable { mutableStateOf(false) }
    val chat = remember { mutableStateListOf<AskMsg>() }  // ponytail: kept in memory only; persist in Room if people want old chats
    BackHandler(open != null || capture || asking || tab != Tab.Home) {
        when { open != null -> open = null; capture -> capture = false; asking -> asking = false; else -> tab = Tab.Home }
    }
    when {
        open != null -> DetailScreen(open!!) { open = null }
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
                    Tab.Home -> HomeScreen(onOpen = { open = it }, onCapture = { capture = true }, onAsk = { asking = true }, onTab = { tab = it })
                    Tab.Memory -> MemoryScreen { open = it }
                    Tab.Insights -> InsightsScreen()
                    Tab.Actions -> ActionsScreen { open = it }
                    Tab.Profile -> ProfileScreen()
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
private fun HomeScreen(onOpen: (Long) -> Unit, onCapture: () -> Unit, onAsk: () -> Unit, onTab: (Tab) -> Unit) {
    val ctx = LocalContext.current
    val dao = remember { Db.get(ctx).dao() }
    val caps by dao.captures().collectAsState(emptyList())
    val tasks by dao.allTasks().collectAsState(emptyList())
    val decisions by dao.allDecisions().collectAsState(emptyList())
    val rec by PhoneRecorder.state.collectAsState()
    val name = remember { Prefs.name(ctx) }
    val start = rememberStartCapture(onCapture)
    val cs = MaterialTheme.colorScheme

    val todayIds = caps.filter { dayOf(it.startedAt) == java.time.LocalDate.now() }.map { it.id }.toSet()
    Box(Modifier.fillMaxSize()) {
        Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(horizontal = 20.dp)) {
            Spacer(Modifier.height(20.dp))
            Row(verticalAlignment = Alignment.CenterVertically) {
                Column(Modifier.weight(1f)) {
                    Text(if (name.isBlank()) greeting() else "${greeting()},\n$name", style = MaterialTheme.typography.headlineSmall, fontWeight = FontWeight.Bold)
                    Text("Your memory, organized.", style = MaterialTheme.typography.bodyMedium, color = cs.onSurfaceVariant)
                }
                Box(Modifier.size(48.dp).clip(CircleShape).background(cs.primary).clickable { onTab(Tab.Profile) }, contentAlignment = Alignment.Center) {
                    Text(name.firstOrNull()?.uppercase() ?: "M", color = cs.onPrimary, fontWeight = FontWeight.Bold, fontSize = 20.sp)
                }
            }
            Spacer(Modifier.height(16.dp))

            Row(
                Modifier.fillMaxWidth().clip(CircleShape).background(cs.surface).border(BorderStroke(1.dp, cs.outlineVariant), CircleShape)
                    .clickable(onClick = onAsk).padding(horizontal = 18.dp, vertical = 14.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Icon(Icons.Rounded.AutoAwesome, null, Modifier.size(20.dp), tint = cs.primary)
                Spacer(Modifier.width(10.dp))
                Text("Ask your memory…", color = cs.onSurfaceVariant, style = MaterialTheme.typography.bodyLarge)
            }
            Spacer(Modifier.height(14.dp))

            // Today's insight
            Column(
                Modifier.fillMaxWidth().clip(RoundedCornerShape(22.dp))
                    .background(Brush.linearGradient(listOf(cs.primaryContainer, cs.secondaryContainer)))
                    .clickable { onTab(Tab.Insights) }.padding(18.dp),
            ) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text("Today's Insight", style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.Bold, color = cs.onPrimaryContainer)
                    Spacer(Modifier.width(6.dp))
                    Icon(Icons.Rounded.AutoAwesome, null, Modifier.size(18.dp), tint = cs.primary)
                    Spacer(Modifier.weight(1f))
                    Icon(Icons.AutoMirrored.Rounded.ArrowForwardIos, null, Modifier.size(14.dp), tint = cs.onPrimaryContainer)
                }
                Spacer(Modifier.height(8.dp))
                Text(
                    if (todayIds.isEmpty()) "Nothing captured yet today. Tap Capture to start."
                    else "${plural(todayIds.size, "capture")}, ${plural(decisions.count { it.captureId in todayIds }, "key decision")}, " +
                        "and ${plural(tasks.count { it.captureId in todayIds }, "action item")}.",
                    style = MaterialTheme.typography.bodyLarge, color = cs.onPrimaryContainer,
                )
            }
            Spacer(Modifier.height(14.dp))

            Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                Stat(Icons.Rounded.Mic, caps.size, "Captures", Modifier.weight(1f)) { onTab(Tab.Memory) }
                Stat(Icons.Rounded.Description, caps.count { it.status == "ready" }, "Summaries", Modifier.weight(1f)) { onTab(Tab.Memory) }
                Stat(Icons.Rounded.CheckCircle, tasks.count { !it.done }, "Actions", Modifier.weight(1f)) { onTab(Tab.Actions) }
            }
            Spacer(Modifier.height(22.dp))

            Row(verticalAlignment = Alignment.CenterVertically) {
                SectionTitle("Recent Captures", Modifier.weight(1f))
                TextButton({ onTab(Tab.Memory) }) { Text("View all") }
            }
            if (caps.isEmpty()) Text("Tap Capture below or record on your watch — captures appear here.",
                style = MaterialTheme.typography.bodyMedium, color = cs.onSurfaceVariant, modifier = Modifier.padding(vertical = 8.dp))
            caps.take(3).forEach { CaptureRow(it, shortWhen(it)) { onOpen(it.id) } }
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
private fun Stat(icon: ImageVector, n: Int, label: String, modifier: Modifier, onClick: () -> Unit) {
    val cs = MaterialTheme.colorScheme
    Column(
        modifier.clip(RoundedCornerShape(18.dp)).background(cs.surface).border(BorderStroke(1.dp, cs.outlineVariant), RoundedCornerShape(18.dp))
            .clickable(onClick = onClick).padding(vertical = 14.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        Icon(icon, null, Modifier.size(22.dp), tint = cs.onSurface)
        Spacer(Modifier.height(6.dp))
        Text("$n", style = MaterialTheme.typography.titleLarge, fontWeight = FontWeight.Bold)
        Text(label, style = MaterialTheme.typography.labelMedium, color = cs.onSurfaceVariant)
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
