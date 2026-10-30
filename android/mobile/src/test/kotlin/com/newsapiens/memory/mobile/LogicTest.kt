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
       "summary":{"executive_summary":"A short sync.","topics":["pricing","launch"]},
       "decisions":[{"text":"Use three tiers"}],
       "tasks":[{"title":"Send copy","assignee_name":"Rahul","due_date":"2026-11-06","due_text":"by Friday","priority":"high"},
                {"title":"Review draft","assignee_name":null,"due_date":null,"due_text":"next week","priority":null}]}
    """

    @Test fun parsesFullResponse() {
        val p = parseProcess(body)
        assertEquals("[0:00] Hello\n[1:05] Pricing decided", p.transcript)
        assertEquals("A short sync.", p.summary)
        assertEquals(listOf("pricing", "launch"), p.topics)
        assertEquals(listOf("Use three tiers"), p.decisions)
        assertEquals(ParsedTask("Send copy", "Rahul", "2026-11-06", "by Friday", "high"), p.tasks[0])
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
}
