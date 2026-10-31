package com.newsapiens.memory.mobile

import java.time.Instant
import java.time.LocalDate
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import java.time.ZoneOffset
import org.json.JSONObject

data class ParsedTask(val title: String, val assignee: String?, val dueDate: String?, val dueText: String?, val priority: String)

data class Parsed(
    val durationS: Double?, val language: String?, val transcript: String, val summary: String,
    val topics: List<String>, val decisions: List<String>, val tasks: List<ParsedTask>, val note: String?,
)

private fun JSONObject.str(k: String): String? = if (isNull(k)) null else optString(k).ifBlank { null }

/** Maps the POST /api/process response onto what the phone stores. */
fun parseProcess(body: String): Parsed {
    val j = JSONObject(body)
    val segs = j.optJSONArray("segments")
    val transcript = (0 until (segs?.length() ?: 0)).joinToString("\n") {
        val s = segs!!.getJSONObject(it)
        val ms = s.getLong("start_ms")
        "[%d:%02d] %s".format(ms / 60000, ms / 1000 % 60, s.getString("text"))
    }
    val sm = j.optJSONObject("summary")
    val topics = sm?.optJSONArray("topics")
    val decisions = j.optJSONArray("decisions")
    val tasks = j.optJSONArray("tasks")
    return Parsed(
        durationS = if (j.isNull("duration_s")) null else j.optDouble("duration_s"),
        language = j.str("language"),
        transcript = transcript,
        summary = sm?.str("executive_summary") ?: j.str("note") ?: "",
        topics = (0 until (topics?.length() ?: 0)).map { topics!!.getString(it) },
        decisions = (0 until (decisions?.length() ?: 0)).map { decisions!!.getJSONObject(it).getString("text") },
        tasks = (0 until (tasks?.length() ?: 0)).map {
            val t = tasks!!.getJSONObject(it)
            ParsedTask(t.getString("title"), t.str("assignee_name"), t.str("due_date"), t.str("due_text"), t.str("priority") ?: "medium")
        },
        note = j.str("note"),
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
