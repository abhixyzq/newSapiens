package com.newsapiens.memory.mobile

import java.time.LocalDate
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class LogicTest {
    private val body = """
      {"language":"en","duration_s":15.0,"engine":"llm:x","note":null,
       "segments":[{"idx":0,"start_ms":0,"end_ms":4000,"speaker":null,"text":"Hello","confidence":0.9},
                   {"idx":1,"start_ms":65000,"end_ms":69000,"speaker":null,"text":"Pricing decided","confidence":null}],
       "summary":{"title":"Pricing sync","executive_summary":"A short sync.","topics":["pricing","launch"]},
       "decisions":[{"text":"Use three tiers","segment_ids":["1"],"timestamp_ms":65000,"confidence":0.8}],
       "insights":[{"kind":"question","text":"What about ads?","segment_ids":["1"],"timestamp_ms":65000,"confidence":0.9}],
       "events":[{"title":"Pricing decision","description":"Picked tiers.","category":"work","importance":0.9,"segment_ids":["1"],"start_ms":65000,"end_ms":69000}],
       "tasks":[{"title":"Send copy","assignee_name":"Rahul","due_date":"2026-11-06","due_text":"by Friday","due_time":"17:00","priority":"high"},
                {"title":"Review draft","assignee_name":null,"due_date":null,"due_text":"next week","priority":null}]}
    """

    @Test fun parsesFullResponse() {
        val p = parseProcess(body)
        assertEquals("[0:00] Hello\n[1:05] Pricing decided", p.transcript)
        assertEquals("A short sync.", p.summary)
        assertEquals(listOf("pricing", "launch"), p.topics)
        assertEquals(ParsedMoment("decision", "Decision", "Use three tiers", 65000, 0.8, 1), p.moments.first { it.category == "decision" })
        assert(p.moments.none { it.text == "What about ads?" })  // questions are not moments
        assertEquals(ParsedEvent("Pricing decision", "Picked tiers.", "work", 65000, 69000, 0.9, 1), p.events.single())
        assertEquals("Pricing sync", p.title)
        assertEquals(8.0, p.speechS!!, 0.0)  // speech only, not the 15 s recording
        assertEquals(ParsedTask("Send copy", "Rahul", "2026-11-06", "by Friday", "high", dueTime = "17:00"), p.tasks[0])
        assertNull(p.tasks[1].dueDate)  // spoken phrase stays a phrase, never becomes a date
        assertEquals("next week", p.tasks[1].dueText)
        assertEquals("medium", p.tasks[1].priority)
    }

    @Test fun silenceResponseHasNoteNotSummary() {
        val p = parseProcess("""{"language":null,"duration_s":null,"segments":[],"engine":null,"note":"No speech detected","summary":null,"decisions":[],"tasks":[]}""")
        assertEquals("No speech detected", p.summary)
        assertEquals("", p.transcript)
        assertNull(p.durationS)
    }

    @Test fun ftsQueryIsSafeAndPrefixed() {
        assertEquals("pric* copy*", ftsQuery("pric  copy"))
        assertEquals("a* OR* b*", ftsQuery("a OR \"b\" -- *"))  // operators and quotes are neutralised into plain words
        assertNull(ftsQuery("  \"*- "))
    }

    @Test fun allDayRangeIsUtcDay() {
        val (s, e) = allDayRange(LocalDate.of(2026, 11, 6))
        assertEquals(24 * 3600 * 1000L, e - s)
        assertEquals(1793923200000L, s)  // 2026-11-06T00:00:00Z
    }

    @Test fun uiHelpers() {
        assertEquals("12:36", clock(756_000))
        assertEquals("1:00:05", clock(3_605_000))
        val today = LocalDate.of(2026, 10, 31)
        assertEquals("Today", ageGroup(today, today))
        assertEquals("This Week", ageGroup(today.minusDays(6), today))
        assertEquals("Last Week", ageGroup(today.minusDays(7), today))
        assertEquals("Earlier", ageGroup(today.minusDays(14), today))
        assertEquals("TNP portal goes live on 10 Nov", titleOf("TNP portal goes live on 10 Nov. Outreach next."))
        assertEquals("Untitled capture", titleOf(" "))
        assertEquals(listOf("SIH" to 3, "launch" to 1, "Others" to 1), topicShare(listOf("SIH, launch", "sih", "Sih,hiring"), 2))
    }

    @Test fun memoryTextIsCompact() {
        val c = Capture(0, 0, "ready", summary = "Pricing review.", topics = "pricing", transcript = "a".repeat(2000))
        val t = memoryText(c, listOf("Three tiers"), listOf("Send copy"), java.time.ZoneOffset.UTC)
        assert(t.startsWith("1970-01-01 Thu 00:00\nSummary: Pricing review.\nTopics: pricing\nDecision: Three tiers\nTask: Send copy\nTranscript: aaa"))
        assertEquals(1500, t.substringAfter("Transcript: ").length)
    }

    @Test fun timelineHelpers() {
        fun ev(title: String, s: Long, e: Long, cat: String = "work") = Event(captureId = 1, title = title, description = "", category = cat, startMs = s, endMs = e, importance = 0.5, line = null)
        val merged = mergeEvents(listOf(ev("API work", 0, 1_000), ev("api work ", 60_000, 90_000), ev("API work", 400_000, 500_000), ev("Lunch", 500_000, 600_000, "personal")))
        assertEquals(listOf(0L to 90_000L, 400_000L to 500_000L, 500_000L to 600_000L), merged.map { it.startMs to it.endMs })

        val min = 60_000L
        val caps = listOf(Capture(0, 0, "ready", durationS = 1800.0), Capture(30 * min + 1000, 30 * min + 1000, "ready", durationS = 60.0),
            Capture(120 * min, 120 * min, "ready", durationS = 60.0))
        assertEquals(2, sessions(caps))  // the 30-min roll continues the first session

        assertEquals(listOf("22s", "12m", "4h 20m"), listOf(22.9, 720.0, 15600.0).map(::spokenText))
        assertEquals(65_000L, lineOffsetMs("[1:05] Pricing decided"))
        assertNull(lineOffsetMs("no offset"))

        val today = LocalDate.of(2026, 10, 31)
        fun task(title: String, due: String?, text: String? = null, done: Boolean = false) = ActionItem(captureId = 1, title = title, assignee = null, dueDate = due, dueText = text, priority = "medium", done = done)
        val (now, later, ask) = taskBuckets(listOf(task("a", null), task("b", "2026-10-31"), task("c", "2026-11-02"), task("d", null, "next week"), task("e", null, done = true)), today)
        assertEquals(listOf("a", "b"), now.map { it.title })
        assertEquals(listOf("c"), later.map { it.title })
        assertEquals(listOf("d"), ask.map { it.title })

        val (from, to) = dayRange(today, java.time.ZoneId.of("Asia/Kolkata"))
        assertEquals(1793385000000L, from)  // 2026-10-31T00:00+05:30
        assertEquals(24 * 3600 * 1000L, to - from)
        val items = dayItems(listOf(Capture(from + 3_600_000, from + 3_600_000, "ready", summary = "Planned launch.", title = "Launch")),
            listOf(ev("Small talk", from, from).copy(importance = 0.1)), java.time.ZoneId.of("Asia/Kolkata"))
        assertEquals(listOf("01:00 Launch: Planned launch."), items)  // low-importance chatter is not sent
    }
}
