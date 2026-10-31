package com.newsapiens.memory.mobile

import android.Manifest
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.rounded.ArrowBack
import androidx.compose.material.icons.automirrored.rounded.FormatListBulleted
import androidx.compose.material.icons.rounded.AutoAwesome
import androidx.compose.material.icons.rounded.CalendarMonth
import androidx.compose.material.icons.rounded.CheckCircle
import androidx.compose.material.icons.rounded.DeleteOutline
import androidx.compose.material.icons.rounded.Description
import androidx.compose.material.icons.rounded.Lock
import androidx.compose.material.icons.rounded.Search
import androidx.compose.material.icons.rounded.Star
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Checkbox
import androidx.compose.material3.CheckboxDefaults
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FilterChip
import androidx.compose.material3.FilterChipDefaults
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.OutlinedTextFieldDefaults
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextDecoration
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import java.time.LocalDate
import java.time.ZoneId
import androidx.compose.foundation.ExperimentalFoundationApi
import androidx.compose.foundation.relocation.BringIntoViewRequester
import androidx.compose.foundation.relocation.bringIntoViewRequester
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.material.icons.automirrored.rounded.KeyboardArrowLeft
import androidx.compose.material.icons.automirrored.rounded.KeyboardArrowRight
import androidx.compose.material3.DatePicker
import androidx.compose.material3.DatePickerDialog
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.TextButton
import androidx.compose.material3.rememberDatePickerState
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.style.TextOverflow
import java.time.Instant
import java.time.ZoneOffset
import java.time.format.DateTimeFormatter
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch

@Composable
private fun Header(title: String) =
    Text(title, Modifier.padding(top = 20.dp, bottom = 14.dp), style = MaterialTheme.typography.headlineSmall, fontWeight = FontWeight.Bold)

@Composable
private fun Card(modifier: Modifier = Modifier, content: @Composable () -> Unit) {
    val cs = MaterialTheme.colorScheme
    Column(
        modifier.fillMaxWidth().clip(RoundedCornerShape(18.dp)).background(cs.surface)
            .border(BorderStroke(1.dp, cs.outlineVariant), RoundedCornerShape(18.dp)).padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(8.dp),
    ) { content() }
}

@Composable
private fun IconTitle(icon: ImageVector, text: String) = Row(verticalAlignment = Alignment.CenterVertically) {
    Icon(icon, null, Modifier.size(18.dp), tint = MaterialTheme.colorScheme.onSurface)
    Spacer(Modifier.width(8.dp))
    SectionTitle(text)
}

// ---------- Memory ----------

private val memoryFilters = listOf("All", "Decisions", "Tasks", "Processing")

@Composable
fun MemoryScreen(onOpen: (Long) -> Unit) {
    val ctx = LocalContext.current
    val dao = remember { Db.get(ctx).dao() }
    val all by dao.captures().collectAsState(emptyList())
    val tasks by dao.allTasks().collectAsState(emptyList())
    val decisions by dao.allDecisions().collectAsState(emptyList())
    var query by rememberSaveable { mutableStateOf("") }
    var filter by rememberSaveable { mutableIntStateOf(0) }
    var hits by remember { mutableStateOf<List<Capture>?>(null) }
    LaunchedEffect(query) { hits = ftsQuery(query)?.let { dao.search(it) } }

    val taskN = tasks.groupingBy { it.captureId }.eachCount()
    val decN = decisions.groupingBy { it.captureId }.eachCount()
    val shown = (hits ?: all).filter {
        when (filter) { 1 -> (decN[it.id] ?: 0) > 0; 2 -> (taskN[it.id] ?: 0) > 0; 3 -> it.status != "ready"; else -> true }
    }
    val today = LocalDate.now()
    val cs = MaterialTheme.colorScheme

    Column(Modifier.fillMaxSize().padding(horizontal = 20.dp)) {
        Header("Search")
        OutlinedTextField(
            query, { query = it }, Modifier.fillMaxWidth(), singleLine = true, shape = RoundedCornerShape(16.dp),
            placeholder = { Text("Search your memories…") }, leadingIcon = { Icon(Icons.Rounded.Search, null) },
            colors = OutlinedTextFieldDefaults.colors(unfocusedContainerColor = cs.surface, focusedContainerColor = cs.surface, unfocusedBorderColor = cs.outlineVariant),
        )
        Row(Modifier.horizontalScroll(rememberScrollState()).padding(vertical = 10.dp), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            memoryFilters.forEachIndexed { i, label ->
                FilterChip(
                    i == filter, { filter = i }, { Text(label) }, shape = CircleShape,
                    colors = FilterChipDefaults.filterChipColors(selectedContainerColor = cs.onBackground, selectedLabelColor = cs.background),
                )
            }
        }
        if (shown.isEmpty()) Text(if (query.isBlank()) "Nothing here yet." else "Nothing found.", color = cs.onSurfaceVariant, modifier = Modifier.padding(top = 12.dp))
        LazyColumn(contentPadding = PaddingValues(bottom = 16.dp)) {
            shown.groupBy { ageGroup(dayOf(it.startedAt), today) }.forEach { (group, caps) ->
                item(key = group) { SectionTitle(group, Modifier.padding(top = 10.dp, bottom = 2.dp)) }
                items(caps, key = { it.id }) { c ->
                    val pts = (decN[c.id] ?: 0) + (taskN[c.id] ?: 0)
                    CaptureRow(c, listOfNotNull(minutes(c), if (pts > 0) plural(pts, "key point") else null).joinToString(" · ").ifBlank { shortWhen(c) }) { onOpen(c.id) }
                }
            }
        }
    }
}

// ---------- Detail ----------

@OptIn(ExperimentalMaterial3Api::class, ExperimentalLayoutApi::class)
@Composable
fun DetailScreen(id: Long, focusLine: Int? = null, onBack: () -> Unit) {
    val ctx = LocalContext.current
    val dao = remember { Db.get(ctx).dao() }
    val scope = rememberCoroutineScope()
    val cap by dao.captureFlow(id).collectAsState(null)
    val decisions by dao.decisions(id).collectAsState(emptyList())
    val tasks by dao.tasks(id).collectAsState(emptyList())
    var msg by remember { mutableStateOf<String?>(null) }
    var tab by rememberSaveable { mutableIntStateOf(if (focusLine != null) 1 else 0) }  // opened from a moment: show its evidence
    val cs = MaterialTheme.colorScheme

    fun addToCalendar() = scope.launch {
        val n = CalendarSync.addDue(ctx, dao, dao.unsyncedDue(id))
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
    Scaffold(
        containerColor = cs.background,
        topBar = {
            TopAppBar(
                title = {}, colors = TopAppBarDefaults.topAppBarColors(containerColor = cs.background),
                navigationIcon = { IconButton(onBack) { Icon(Icons.AutoMirrored.Rounded.ArrowBack, "Back") } },
                actions = {
                    IconButton({ scope.launch { Files.audio(ctx, id).delete(); dao.deleteCapture(id); onBack() } }) {
                        Icon(Icons.Rounded.DeleteOutline, "Delete capture")
                    }
                },
            )
        },
    ) { pad ->
        if (c == null) { Text("Capture deleted.", Modifier.padding(pad).padding(20.dp)); return@Scaffold }
        Column(Modifier.padding(pad).padding(horizontal = 20.dp).verticalScroll(rememberScrollState()), verticalArrangement = Arrangement.spacedBy(14.dp)) {
            Text(captureTitle(c), style = MaterialTheme.typography.headlineSmall, fontWeight = FontWeight.Bold)
            Row(verticalAlignment = Alignment.CenterVertically) {
                Icon(Icons.Rounded.CalendarMonth, null, Modifier.size(16.dp), tint = cs.onSurfaceVariant)
                Spacer(Modifier.width(6.dp))
                Text(listOfNotNull(whenText(c.startedAt), minutes(c)).joinToString(" · "), style = MaterialTheme.typography.bodyMedium, color = cs.onSurfaceVariant)
            }
            c.processedAt?.let {  // processing time is shown, never used as the conversation time
                Text("Recorded ${timeText(c.startedAt)} · analysed ${timeText(it)}", style = MaterialTheme.typography.labelSmall, color = cs.onSurfaceVariant)
            }
            if (c.status != "ready") {
                Card { Text(statusText(c)) }
                if (c.status == "failed") Button({ scope.launch { dao.updateCapture(c.copy(status = "queued", error = null)); ProcessWorker.enqueue(ctx, id) } }) { Text("Retry") }
                return@Column
            }
            Segmented(listOf("Summary", "Transcript"), tab, { tab = it }, Modifier.fillMaxWidth())
            if (tab == 1) {
                Card { Transcript(c, focusLine) }
            } else {
                Card {
                    IconTitle(Icons.Rounded.Description, "Summary")
                    Text(c.summary, style = MaterialTheme.typography.bodyMedium, lineHeight = 22.sp)
                    if (c.topics.isNotBlank()) FlowRow(horizontalArrangement = Arrangement.spacedBy(6.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                        c.topics.split(",").map { it.trim() }.filter { it.isNotEmpty() }.forEach { Chip(it) }
                    }
                }
                if (decisions.isNotEmpty()) Column(
                    Modifier.fillMaxWidth().clip(RoundedCornerShape(18.dp)).background(cs.tertiaryContainer).padding(16.dp),
                    verticalArrangement = Arrangement.spacedBy(6.dp),
                ) {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Icon(Icons.Rounded.CheckCircle, null, Modifier.size(18.dp), tint = cs.onTertiaryContainer)
                        Spacer(Modifier.width(8.dp))
                        Text("Decisions", style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.Bold, color = cs.onTertiaryContainer)
                    }
                    decisions.forEach { Text("•  ${it.text}", color = cs.onTertiaryContainer) }
                }
                if (tasks.isNotEmpty()) Card {
                    IconTitle(Icons.AutoMirrored.Rounded.FormatListBulleted, "Action Items")
                    tasks.forEach { t -> TaskRow(t) { scope.launch { dao.setDone(t.id, it) } } }
                    Text("Extracted by AI — check before relying on it.", style = MaterialTheme.typography.labelSmall, color = cs.onSurfaceVariant)
                    if (tasks.any { it.dueDate != null && it.calendarEventId == null }) OutlinedButton({
                        if (CalendarSync.hasPermission(ctx)) addToCalendar()
                        else perms.launch(arrayOf(Manifest.permission.READ_CALENDAR, Manifest.permission.WRITE_CALENDAR))
                    }) { Text("Add dated tasks to calendar") }
                    msg?.let { Text(it, style = MaterialTheme.typography.labelMedium) }
                }
            }
            Spacer(Modifier.height(16.dp))
        }
    }
}

@Composable
private fun Chip(text: String) = Text(
    text, Modifier.clip(CircleShape).background(MaterialTheme.colorScheme.primaryContainer).padding(horizontal = 12.dp, vertical = 6.dp),
    style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.onPrimaryContainer,
)

@Composable
fun TaskRow(t: ActionItem, onClick: (() -> Unit)? = null, onToggle: (Boolean) -> Unit) {
    val cs = MaterialTheme.colorScheme
    Row(Modifier.fillMaxWidth().clip(RoundedCornerShape(12.dp)).then(if (onClick != null) Modifier.clickable(onClick = onClick) else Modifier),
        verticalAlignment = Alignment.CenterVertically) {
        Checkbox(t.done, onToggle, colors = CheckboxDefaults.colors(checkedColor = cs.primary))
        Column(Modifier.weight(1f).padding(vertical = 6.dp)) {
            Text(t.title, style = MaterialTheme.typography.bodyMedium, fontWeight = FontWeight.Medium,
                textDecoration = if (t.done) TextDecoration.LineThrough else null, color = if (t.done) cs.onSurfaceVariant else cs.onSurface)
            if (t.dueDate == null && t.dueText != null && !t.done)  // spoken date, never guessed: the user picks the real one
                Text("Confirm date: “${t.dueText}”", style = MaterialTheme.typography.labelMedium, color = cs.error)
            val meta = listOfNotNull(t.assignee, t.dueDate?.let { "Due: ${dueText(it)}" + (t.dueTime?.let { tm -> " $tm" } ?: "") }, if (t.calendarEventId != null) "✓ reminder" else null,
                t.atMs?.let { "said ${shortDay(it)} ${timeText(it)}" }).joinToString(" · ")
            if (meta.isNotEmpty()) Text(meta, style = MaterialTheme.typography.labelMedium, color = if (t.dueDate != null && !t.done) cs.error else cs.onSurfaceVariant)
        }
    }
}

private val TimeCol = 76.dp  // fits "12:05 AM"

/** Transcript with each line at its real clock time; [focus] (the evidence line) is highlighted and scrolled into view. */
@OptIn(ExperimentalFoundationApi::class)
@Composable
private fun Transcript(c: Capture, focus: Int?) {
    val cs = MaterialTheme.colorScheme
    val lines = c.transcript.lines().filter { it.isNotBlank() }
    if (lines.isEmpty()) { Text("(empty)"); return }
    val bring = remember { BringIntoViewRequester() }
    LaunchedEffect(focus) { if (focus != null) { delay(150); bring.bringIntoView() } }
    lines.forEachIndexed { i, l ->
        val off = lineOffsetMs(l)
        val on = i == focus
        Row(Modifier.fillMaxWidth().clip(RoundedCornerShape(8.dp)).background(if (on) cs.primaryContainer else Color.Transparent)
            .then(if (on) Modifier.bringIntoViewRequester(bring) else Modifier).padding(horizontal = 6.dp, vertical = 3.dp)) {
            Text(off?.let { timeText(c.startedAt + it) } ?: "", Modifier.width(TimeCol), style = MaterialTheme.typography.labelMedium, color = cs.onSurfaceVariant)
            Text(l.substringAfter("] ", l), style = MaterialTheme.typography.bodyMedium, lineHeight = 21.sp)
        }
    }
}

/** "10:35  Decision  ·  Picked PostgreSQL" row used on Home. */
@Composable
fun MomentRow(m: Moment, onClick: () -> Unit) {
    val cs = MaterialTheme.colorScheme
    Row(Modifier.fillMaxWidth().clip(RoundedCornerShape(12.dp)).clickable(onClick = onClick).padding(vertical = 8.dp, horizontal = 2.dp)) {
        Text(timeText(m.atMs), Modifier.width(TimeCol), style = MaterialTheme.typography.labelMedium, color = cs.onSurfaceVariant)
        Column(Modifier.weight(1f)) {
            Text(m.title, style = MaterialTheme.typography.labelLarge, fontWeight = FontWeight.SemiBold, color = cs.primary)
            Text(m.text, style = MaterialTheme.typography.bodyMedium, maxLines = 2, overflow = TextOverflow.Ellipsis)
        }
    }
}

// ---------- Timeline ----------

@Composable
fun TimelineScreen(onOpen: (Long, Int?) -> Unit) {
    val ctx = LocalContext.current
    val dao = remember { Db.get(ctx).dao() }
    var day by rememberSaveable { mutableStateOf(LocalDate.now().toString()) }
    val d = LocalDate.parse(day)
    val (from, to) = remember(day) { dayRange(d) }
    val caps by remember(day) { dao.capturesBetween(from, to) }.collectAsState(emptyList())
    val stored by remember(day) { dao.eventsBetween(from, to) }.collectAsState(emptyList())
    val cs = MaterialTheme.colorScheme

    // captures analysed before events existed still show, as one event spanning the real recording
    val withEvents = stored.map { it.captureId }.toSet()
    val legacy = caps.filter { it.status == "ready" && it.id !in withEvents && it.transcript.isNotBlank() }.map {
        Event(captureId = it.id, title = captureTitle(it), description = "", category = "other", startMs = it.startedAt,
            endMs = it.startedAt + ((it.durationS ?: 0.0) * 1000).toLong(), importance = 0.5, line = null)
    }
    val events = mergeEvents((stored.filter { it.importance >= 0.3 } + legacy).sortedBy { it.startMs })
    val pending = caps.count { it.status != "ready" && it.status != "failed" }

    Column(Modifier.fillMaxSize().padding(horizontal = 20.dp)) {
        Row(Modifier.padding(top = 12.dp, bottom = 6.dp), verticalAlignment = Alignment.CenterVertically) {
            Text("Timeline", Modifier.weight(1f), style = MaterialTheme.typography.headlineSmall, fontWeight = FontWeight.Bold)
            IconButton({ day = d.minusDays(1).toString() }) { Icon(Icons.AutoMirrored.Rounded.KeyboardArrowLeft, "Previous day") }
            Text(when (d) { LocalDate.now() -> "Today"; LocalDate.now().minusDays(1) -> "Yesterday"; else -> d.format(DateTimeFormatter.ofPattern("EEE, d MMM")) },
                style = MaterialTheme.typography.labelLarge)
            IconButton({ day = d.plusDays(1).toString() }, enabled = d < LocalDate.now()) { Icon(Icons.AutoMirrored.Rounded.KeyboardArrowRight, "Next day") }
        }
        if (pending > 0) Text("${plural(pending, "capture")} still processing…", style = MaterialTheme.typography.bodySmall, color = cs.onSurfaceVariant)
        if (events.isEmpty()) Text("Nothing captured this day.", color = cs.onSurfaceVariant, modifier = Modifier.padding(top = 16.dp))
        LazyColumn(contentPadding = PaddingValues(vertical = 8.dp)) {
            itemsIndexed(events, key = { _, e -> "${e.captureId}-${e.startMs}-${e.title}" }) { i, e ->
                val gap = if (i > 0) (e.startMs - events.take(i).maxOf { it.endMs }) / 60_000 else 0
                if (gap >= 15) Text("— ${if (gap >= 60) "${gap / 60}h ${gap % 60}m" else "${gap}m"} with nothing captured —",
                    Modifier.padding(start = TimeCol, top = 4.dp, bottom = 8.dp), style = MaterialTheme.typography.labelSmall, color = cs.onSurfaceVariant)
                Row(Modifier.fillMaxWidth().clip(RoundedCornerShape(12.dp)).clickable { onOpen(e.captureId, e.line) }.padding(vertical = 8.dp)) {
                    Column(Modifier.width(TimeCol)) {
                        Text(timeText(e.startMs), style = MaterialTheme.typography.labelMedium, fontWeight = FontWeight.SemiBold)
                        Text(timeText(e.endMs), style = MaterialTheme.typography.labelSmall, color = cs.onSurfaceVariant)
                    }
                    Box(Modifier.padding(top = 5.dp, end = 12.dp).size(8.dp).clip(CircleShape).background(cs.primary))
                    Column(Modifier.weight(1f)) {
                        Text(e.title, style = MaterialTheme.typography.titleSmall, fontWeight = FontWeight.SemiBold)
                        Text("${e.category.replaceFirstChar { it.uppercase() }} · ${spokenText((e.endMs - e.startMs) / 1000.0)}",
                            style = MaterialTheme.typography.labelSmall, color = cs.onSurfaceVariant)
                        if (e.description.isNotBlank()) Text(e.description, style = MaterialTheme.typography.bodySmall, modifier = Modifier.padding(top = 2.dp))
                    }
                }
            }
            item {
                Text("Times are when you spoke, not when it was analysed. Titles are AI-written — tap an event to read the transcript.",
                    Modifier.padding(top = 12.dp), style = MaterialTheme.typography.labelSmall, color = cs.onSurfaceVariant)
            }
        }
    }
}

// ---------- Insights ----------

@OptIn(ExperimentalLayoutApi::class)
@Composable
fun InsightsScreen(onBack: () -> Unit) {
    val ctx = LocalContext.current
    val dao = remember { Db.get(ctx).dao() }
    val all by dao.captures().collectAsState(emptyList())
    val tasks by dao.allTasks().collectAsState(emptyList())
    var range by rememberSaveable { mutableIntStateOf(1) }
    val cs = MaterialTheme.colorScheme

    val today = LocalDate.now()
    val from = when (range) { 0 -> today; 1 -> today.minusDays(6); else -> today.minusDays(29) }
    val since = from.atStartOfDay(ZoneId.systemDefault()).toInstant().toEpochMilli()
    val caps = all.filter { it.startedAt >= since }
    val ids = caps.map { it.id }.toSet()
    val mins = caps.sumOf { it.durationS ?: 0.0 }.toInt() / 60
    val share = topicShare(caps.map { it.topics }, 4)
    val themes = topicShare(caps.map { it.topics }, Int.MAX_VALUE)
    val total = share.sumOf { it.second }.coerceAtLeast(1)
    val period = listOf("Today", "This week", "This month")[range]

    Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(horizontal = 20.dp), verticalArrangement = Arrangement.spacedBy(14.dp)) {
        Row(Modifier.padding(top = 8.dp), verticalAlignment = Alignment.CenterVertically) {
            IconButton(onBack) { Icon(Icons.AutoMirrored.Rounded.ArrowBack, "Back") }
            Text("Insights", style = MaterialTheme.typography.headlineSmall, fontWeight = FontWeight.Bold)
        }
        Segmented(listOf("Day", "Week", "Month"), range, { range = it }, Modifier.fillMaxWidth())
        Column(Modifier.fillMaxWidth().clip(RoundedCornerShape(20.dp)).background(cs.primaryContainer).padding(18.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Icon(Icons.Rounded.AutoAwesome, null, Modifier.size(20.dp), tint = cs.primary)
                Spacer(Modifier.width(8.dp))
                Text("${listOf("Daily", "Weekly", "Monthly")[range]} Recap", fontWeight = FontWeight.Bold, color = cs.onPrimaryContainer)
            }
            Spacer(Modifier.height(6.dp))
            Text(
                "$period you had ${plural(caps.size, "capture")} (${mins / 60}h ${mins % 60}m), discussed ${plural(themes.size, "topic")}, " +
                    "and created ${plural(tasks.count { it.captureId in ids }, "action item")}.",
                color = cs.onPrimaryContainer,
            )
        }
        Card {
            SectionTitle("Topic Breakdown")
            if (share.isEmpty()) Text("No topics yet for this period.", color = cs.onSurfaceVariant)
            else Row(verticalAlignment = Alignment.CenterVertically) {
                Box(Modifier.size(130.dp), contentAlignment = Alignment.Center) {
                    Canvas(Modifier.fillMaxSize()) {
                        val w = 18.dp.toPx()
                        var start = -90f
                        share.forEachIndexed { i, (_, n) ->
                            val sweep = 360f * n / total
                            drawArc(TopicColors[i % TopicColors.size], start, sweep - 2f, false,
                                topLeft = Offset(w / 2, w / 2), size = Size(size.width - w, size.height - w), style = Stroke(w))
                            start += sweep
                        }
                    }
                    Column(horizontalAlignment = Alignment.CenterHorizontally) {
                        Text("${caps.size}", fontSize = 26.sp, fontWeight = FontWeight.Bold)
                        Text("Captures", style = MaterialTheme.typography.labelSmall, color = cs.onSurfaceVariant)
                    }
                }
                Spacer(Modifier.width(16.dp))
                Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    share.forEachIndexed { i, (name, n) ->
                        Row(verticalAlignment = Alignment.CenterVertically) {
                            Box(Modifier.size(9.dp).clip(CircleShape).background(TopicColors[i % TopicColors.size]))
                            Spacer(Modifier.width(8.dp))
                            Text(name, Modifier.weight(1f), style = MaterialTheme.typography.labelLarge, maxLines = 1)
                            Text("${100 * n / total}%", style = MaterialTheme.typography.labelLarge, color = cs.onSurfaceVariant)
                        }
                    }
                }
            }
        }
        if (share.isNotEmpty()) {
            SectionTitle("Top Themes")
            FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                themes.take(8).forEach { Chip(it.first) }
            }
        }
        Spacer(Modifier.height(16.dp))
    }
}

// ---------- Tasks ----------

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun TasksScreen(onOpen: (Long, Int?) -> Unit) {
    val ctx = LocalContext.current
    val dao = remember { Db.get(ctx).dao() }
    val scope = rememberCoroutineScope()
    val tasks by dao.allTasks().collectAsState(emptyList())
    var filter by rememberSaveable { mutableIntStateOf(0) }
    var editing by remember { mutableStateOf<ActionItem?>(null) }
    val (now, upcoming, unconfirmed) = taskBuckets(tasks, LocalDate.now())
    val shown = when (filter) { 0 -> unconfirmed + now; 1 -> upcoming; else -> tasks.filter { it.done } }

    Column(Modifier.fillMaxSize().padding(horizontal = 20.dp)) {
        Header("Tasks")
        Segmented(listOf("To do (${now.size + unconfirmed.size})", "Upcoming (${upcoming.size})", "Done"), filter, { filter = it }, Modifier.fillMaxWidth())
        Spacer(Modifier.height(8.dp))
        if (shown.isEmpty()) Text(if (filter == 2) "Nothing done yet." else "All clear.",
            color = MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.padding(top = 16.dp))
        LazyColumn(contentPadding = PaddingValues(bottom = 16.dp)) {
            items(shown, key = { it.id }) { t -> TaskRow(t, onClick = { editing = t }) { scope.launch { dao.setDone(t.id, it) } } }
            item { Text("Extracted by AI from your conversations — tap one to edit, date, remind or delete it.",
                Modifier.padding(top = 10.dp), style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant) }
        }
    }
    editing?.let { t -> TaskSheet(t, { editing = null }) { onOpen(t.captureId, t.line) } }
}

/** Edit / date / remind / delete one task. Dates come only from the user here; spoken phrases stay as hints. */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun TaskSheet(t: ActionItem, onDismiss: () -> Unit, onSource: () -> Unit) {
    val ctx = LocalContext.current
    val dao = remember { Db.get(ctx).dao() }
    val scope = rememberCoroutineScope()
    var title by remember { mutableStateOf(t.title) }
    var desc by remember { mutableStateOf(t.description) }
    var due by remember { mutableStateOf(t.dueDate) }
    var picking by remember { mutableStateOf(false) }
    var msg by remember { mutableStateOf<String?>(null) }
    val edited = { t.copy(title = title.trim().ifEmpty { t.title }, description = desc.trim(), dueDate = due) }
    fun remind() = scope.launch {
        dao.updateTask(edited())
        val n = CalendarSync.addDue(ctx, dao, listOf(edited()))
        msg = when { n < 0 -> "No writable calendar on this phone"; n == 0 -> "Already in your calendar"; else -> "Added to your calendar" }
    }
    val perms = rememberLauncherForActivityResult(ActivityResultContracts.RequestMultiplePermissions()) { g -> if (g.values.all { it }) remind() else msg = "Calendar permission denied" }

    ModalBottomSheet(onDismiss, containerColor = MaterialTheme.colorScheme.surface) {
        Column(Modifier.padding(horizontal = 20.dp).padding(bottom = 24.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
            OutlinedTextField(title, { title = it }, Modifier.fillMaxWidth(), label = { Text("Task") }, shape = RoundedCornerShape(14.dp))
            OutlinedTextField(desc, { desc = it }, Modifier.fillMaxWidth(), label = { Text("Description") }, minLines = 2, shape = RoundedCornerShape(14.dp))
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text(due?.let { "Due ${dueText(it)}" + (t.dueTime?.let { tm -> " at $tm" } ?: "") } ?: t.dueText?.let { "Heard “$it” — pick the date" } ?: "No due date",
                    Modifier.weight(1f), style = MaterialTheme.typography.bodyMedium)
                TextButton({ picking = true }) { Text(if (due == null) "Set date" else "Change") }
            }
            t.atMs?.let { Text("Said ${shortDay(it)} at ${timeText(it)}", style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.onSurfaceVariant) }
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                Button({ scope.launch { dao.updateTask(edited()); onDismiss() } }, Modifier.weight(1f)) { Text("Save") }
                OutlinedButton({ onDismiss(); onSource() }) { Text("Transcript") }
            }
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                OutlinedButton({
                    if (due == null) msg = "Set a date first"
                    else if (CalendarSync.hasPermission(ctx)) remind() else perms.launch(arrayOf(Manifest.permission.READ_CALENDAR, Manifest.permission.WRITE_CALENDAR))
                }, Modifier.weight(1f)) { Text("Remind me") }
                OutlinedButton({ scope.launch { dao.deleteTask(t.id); onDismiss() } }, Modifier.weight(1f)) { Text("Delete", color = MaterialTheme.colorScheme.error) }
            }
            msg?.let { Text(it, style = MaterialTheme.typography.labelMedium) }
        }
    }
    if (picking) {
        val state = rememberDatePickerState(initialSelectedDateMillis = due?.let { allDayRange(LocalDate.parse(it)).first })
        DatePickerDialog(
            onDismissRequest = { picking = false },
            confirmButton = { TextButton({ state.selectedDateMillis?.let { due = Instant.ofEpochMilli(it).atZone(ZoneOffset.UTC).toLocalDate().toString() }; picking = false }) { Text("OK") } },
            dismissButton = { TextButton({ picking = false }) { Text("Cancel") } },
        ) { DatePicker(state) }
    }
}

// ---------- Profile ----------

@Composable
fun ProfileScreen() {
    val ctx = LocalContext.current
    var name by remember { mutableStateOf(Prefs.name(ctx)) }
    var saved by remember { mutableStateOf(false) }
    val cs = MaterialTheme.colorScheme

    Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(horizontal = 20.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
        Header("Settings")
        Row(verticalAlignment = Alignment.CenterVertically) {
            Box(Modifier.size(64.dp).clip(CircleShape).background(cs.primary), contentAlignment = Alignment.Center) {
                Text(name.firstOrNull()?.uppercase() ?: "M", color = cs.onPrimary, fontSize = 26.sp, fontWeight = FontWeight.Bold)
            }
            Spacer(Modifier.width(14.dp))
            Column {
                Text(name.ifBlank { "Your name" }, style = MaterialTheme.typography.titleLarge, fontWeight = FontWeight.Bold)
                Text("MindCapture · your AI second brain", color = cs.onSurfaceVariant, style = MaterialTheme.typography.bodySmall)
            }
        }
        Card {
            OutlinedTextField(name, { name = it; saved = false }, Modifier.fillMaxWidth(), label = { Text("Name") }, singleLine = true,
                shape = RoundedCornerShape(14.dp))
            Button(
                { Prefs.save(ctx, name); saved = true }, Modifier.fillMaxWidth().height(50.dp), shape = CircleShape,
                colors = ButtonDefaults.buttonColors(containerColor = cs.onBackground, contentColor = cs.background),
            ) { Text(if (saved) "Saved ✓" else "Save") }
        }
        Card {
            IconTitle(Icons.Rounded.Lock, "Private & secure")
            Text("Audio is uploaded only to your backend for transcription and deleted from the phone as soon as the result is saved.",
                style = MaterialTheme.typography.bodySmall, color = cs.onSurfaceVariant)
        }
        Card {
            IconTitle(Icons.Rounded.Star, "Wear OS")
            Text("Add the Voice memory tile on your watch to capture from your wrist; recordings land here automatically.",
                style = MaterialTheme.typography.bodySmall, color = cs.onSurfaceVariant)
        }
        Spacer(Modifier.height(16.dp))
    }
}
