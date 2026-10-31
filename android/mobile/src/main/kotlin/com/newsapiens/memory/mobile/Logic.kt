package com.newsapiens.memory.mobile

import java.time.Instant
import java.time.LocalDate
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import java.time.ZoneOffset
import org.json.JSONObject

data class ParsedTask(
    val title: String, val assignee: String?, val dueDate: String?, val dueText: String?, val priority: String,
    val description: String = "", val offsetMs: Long? = null, val line: Int? = null, val dueTime: String? = null,
)

/** offsetMs = milliseconds after the recording started (speech time = capture start + offset). */
data class ParsedMoment(val category: String, val title: String, val text: String, val offsetMs: Long, val importance: Double, val line: Int?)
data class ParsedEvent(val title: String, val description: String, val category: String, val startMs: Long, val endMs: Long, val importance: Double, val line: Int?)

data class Parsed(
    val durationS: Double?, val language: String?, val transcript: String, val summary: String,
    val topics: List<String>, val tasks: List<ParsedTask>, val note: String?,
    val title: String = "", val speechS: Double? = null,
    val moments: List<ParsedMoment> = emptyList(), val events: List<ParsedEvent> = emptyList(),
)

private fun JSONObject.str(k: String): String? = if (isNull(k)) null else optString(k).ifBlank { null }
private fun JSONObject.objs(k: String): List<JSONObject> = optJSONArray(k)?.let { a -> (0 until a.length()).map { a.getJSONObject(it) } } ?: emptyList()
/** First cited transcript line; /api/process cites segments by their index. */
private fun JSONObject.line(): Int? = optJSONArray("segment_ids")?.optString(0)?.toIntOrNull()

/** kind -> (category, title, weight); importance = confidence x weight. Mirrors MOMENT_KINDS in backend extraction.py. */
private val momentKinds = mapOf(
    "decision" to Triple("decision", "Decision", 1.0), "task" to Triple("reminder", "Task", 1.0),
    "commitment" to Triple("commitment", "Commitment", 0.9), "idea" to Triple("idea", "Idea", 0.8), "risk" to Triple("work", "Risk", 0.7),
)

private fun moment(kind: String, o: JSONObject, text: String): ParsedMoment? = momentKinds[kind]?.let { (cat, title, w) ->
    ParsedMoment(cat, title, text, o.optLong("timestamp_ms", 0), Math.round(o.optDouble("confidence", 0.5) * w * 100) / 100.0, o.line())
}

/** Maps the POST /api/process response onto what the phone stores. */
fun parseProcess(body: String): Parsed {
    val j = JSONObject(body)
    val segs = j.objs("segments")
    val transcript = segs.joinToString("\n") {
        val ms = it.getLong("start_ms")
        "[%d:%02d] %s".format(ms / 60000, ms / 1000 % 60, it.getString("text"))
    }
    val sm = j.optJSONObject("summary")
    val topics = sm?.optJSONArray("topics")
    val tasks = j.objs("tasks")
    val moments = j.objs("decisions").mapNotNull { moment("decision", it, it.getString("text")) } +
        tasks.mapNotNull { moment("task", it, it.getString("title")) } +
        j.objs("commitments").mapNotNull { moment("commitment", it, it.getString("text")) } +
        j.objs("insights").mapNotNull { moment(it.optString("kind"), it, it.getString("text")) }
    return Parsed(
        durationS = if (j.isNull("duration_s")) null else j.optDouble("duration_s"),
        language = j.str("language"),
        transcript = transcript,
        summary = sm?.str("executive_summary") ?: j.str("note") ?: "",
        topics = (0 until (topics?.length() ?: 0)).map { topics!!.getString(it) },
        tasks = tasks.map {
            ParsedTask(it.getString("title"), it.str("assignee_name"), it.str("due_date"), it.str("due_text"), it.str("priority") ?: "medium",
                it.str("description") ?: "", if (it.has("timestamp_ms")) it.optLong("timestamp_ms") else null, it.line(), it.str("due_time"))
        },
        note = j.str("note"),
        title = sm?.str("title") ?: "",
        speechS = if (segs.isEmpty()) 0.0 else segs.sumOf { it.getLong("end_ms") - it.getLong("start_ms") } / 1000.0,
        moments = moments.sortedBy { it.offsetMs },
        events = j.objs("events").map {
            ParsedEvent(it.getString("title"), it.optString("description"), it.optString("category", "other"),
                it.optLong("start_ms"), it.optLong("end_ms"), it.optDouble("importance", 0.5), it.line())
        },
    )
}

/** Safe FTS4 MATCH expression from free text: alphanumeric words, prefix-matched, AND-ed. Null if nothing searchable. */
fun ftsQuery(input: String): String? =
    Regex("[\\p{L}\\p{N}]+").findAll(input).map { it.value }.joinToString(" ") { "$it*" }.ifEmpty { null }

/** Live timer text: m:ss under an hour, h:mm:ss after. */
fun clock(ms: Long): String {
    val s = ms / 1000
    return if (s >= 3600) "%d:%02d:%02d".format(s / 3600, s / 60 % 60, s % 60) else "%02d:%02d".format(s / 60, s % 60)
}

/** Memory list section for a capture taken on [day], seen on [today]. */
fun ageGroup(day: LocalDate, today: LocalDate): String = when {
    day == today -> "Today"
    day.isAfter(today.minusDays(7)) -> "This Week"
    day.isAfter(today.minusDays(14)) -> "Last Week"
    else -> "Earlier"
}

/** First sentence of the summary as a list title. */
fun titleOf(summary: String): String =
    summary.trim().split(Regex("(?<=[.!?])\\s")).first().removeSuffix(".").ifBlank { "Untitled capture" }

/** Topic counts across captures (comma-joined topic strings), biggest [top] kept, rest folded into "Others". */
fun topicShare(topicLists: List<String>, top: Int): List<Pair<String, Int>> {
    val counts = topicLists.flatMap { it.split(",") }.map { it.trim() }.filter { it.isNotEmpty() }
        .groupBy { it.lowercase() }.values.map { it.first() to it.size }.sortedByDescending { it.second }  // first spelling wins, keeps "SIH"
    val rest = counts.drop(top).sumOf { it.second }
    return counts.take(top) + if (rest > 0) listOf("Others" to rest) else emptyList()
}

/** All-day calendar events are stored as UTC midnight-to-midnight. */
fun allDayRange(date: LocalDate): Pair<Long, Long> {
    val start = date.atStartOfDay(ZoneOffset.UTC).toInstant().toEpochMilli()
    return start to date.plusDays(1).atStartOfDay(ZoneOffset.UTC).toInstant().toEpochMilli()
}

private val askStamp = DateTimeFormatter.ofPattern("yyyy-MM-dd EEE HH:mm")

/** One capture as a compact text block for Ask: when, summary, topics, decisions, tasks, then the head of the transcript. */
fun memoryText(c: Capture, decisions: List<String>, tasks: List<String>, zone: ZoneId = ZoneId.systemDefault()): String = buildString {
    append(Instant.ofEpochMilli(c.startedAt).atZone(zone).format(askStamp))
    if (c.summary.isNotBlank()) append("\nSummary: ").append(c.summary)
    if (c.topics.isNotBlank()) append("\nTopics: ").append(c.topics)
    decisions.forEach { append("\nDecision: ").append(it) }
    tasks.forEach { append("\nTask: ").append(it) }
    if (c.transcript.isNotBlank()) append("\nTranscript: ").append(c.transcript.take(1500))
}

/** [start, end) of a local day in epoch ms. */
fun dayRange(d: LocalDate, zone: ZoneId = ZoneId.systemDefault()): Pair<Long, Long> =
    d.atStartOfDay(zone).toInstant().toEpochMilli() to d.plusDays(1).atStartOfDay(zone).toInstant().toEpochMilli()

/** Joins neighbouring events that are the same activity (same category and title, at most [gapMs] apart), e.g. across the
 *  30-minute segment roll. Input must be sorted by startMs. */
fun mergeEvents(events: List<Event>, gapMs: Long = 120_000): List<Event> = events.fold(mutableListOf()) { out, e ->
    val last = out.lastOrNull()
    if (last != null && last.category == e.category && last.title.trim().equals(e.title.trim(), ignoreCase = true) && e.startMs - last.endMs <= gapMs)
        out[out.size - 1] = last.copy(endMs = maxOf(last.endMs, e.endMs), importance = maxOf(last.importance, e.importance))
    else out += e
    out
}

/** Conversation sessions: captures that continue each other (one starts within 5 s of the previous one ending) count once. */
fun sessions(caps: List<Capture>): Int {
    var n = 0
    var end: Long? = null
    for (c in caps.sortedBy { it.startedAt }) {
        if (end == null || c.startedAt - end > 5_000) n++
        end = c.startedAt + ((c.durationS ?: 0.0) * 1000).toLong()
    }
    return n
}

/** "[1:05] text" -> 65000; null when the line has no offset. */
fun lineOffsetMs(line: String): Long? = Regex("^\\[(\\d+):(\\d{2})]").find(line)?.destructured?.let { (m, s) -> (m.toLong() * 60 + s.toLong()) * 1000 }

/** Open tasks split for the dashboard: (due today/overdue or undated, dated later, spoken date not yet confirmed). */
fun taskBuckets(tasks: List<ActionItem>, today: LocalDate): Triple<List<ActionItem>, List<ActionItem>, List<ActionItem>> {
    val open = tasks.filter { !it.done }
    val due = { t: ActionItem -> t.dueDate?.let { runCatching { LocalDate.parse(it) }.getOrNull() } }
    val unconfirmed = open.filter { it.dueDate == null && it.dueText != null }
    val upcoming = open.filter { due(it)?.isAfter(today) == true }.sortedBy { it.dueDate }
    return Triple(open - unconfirmed.toSet() - upcoming.toSet(), upcoming, unconfirmed)
}

/** Day consolidation input: each capture's title/summary and its events, at speech time, oldest first. */
fun dayItems(caps: List<Capture>, events: List<Event>, zone: ZoneId = ZoneId.systemDefault()): List<String> {
    val hm = DateTimeFormatter.ofPattern("HH:mm")
    val at = { ms: Long -> Instant.ofEpochMilli(ms).atZone(zone).format(hm) }
    return caps.filter { it.status == "ready" && it.summary.isNotBlank() }.map { "${at(it.startedAt)} ${it.title.ifBlank { titleOf(it.summary) }}: ${it.summary.take(600)}" } +
        events.filter { it.importance >= 0.3 }.map { "${at(it.startMs)} ${it.title} — ${it.description}" }
}

/** Speech total for the overview tile: "45s", "12m", "4h 20m". */
fun spokenText(seconds: Double): String {
    val s = seconds.toLong()
    return when { s < 60 -> "${s}s"; s < 3600 -> "${s / 60}m"; else -> "${s / 3600}h ${s / 60 % 60}m" }
}
