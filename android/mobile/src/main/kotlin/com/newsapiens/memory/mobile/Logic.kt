package com.newsapiens.memory.mobile

import java.time.LocalDate
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

/** All-day calendar events are stored as UTC midnight-to-midnight. */
fun allDayRange(date: LocalDate): Pair<Long, Long> {
    val start = date.atStartOfDay(ZoneOffset.UTC).toInstant().toEpochMilli()
    return start to date.plusDays(1).atStartOfDay(ZoneOffset.UTC).toInstant().toEpochMilli()
}
