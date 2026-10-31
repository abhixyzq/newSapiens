package com.newsapiens.memory.mobile

import android.Manifest
import android.content.ContentUris
import android.content.ContentValues
import android.content.Context
import android.content.pm.PackageManager
import android.provider.CalendarContract
import androidx.core.content.ContextCompat
import java.time.LocalDate
import java.time.LocalTime
import java.time.ZoneId

/** Pushes tasks that have a certain due date to the device calendar: a 30-minute event with an alert at the spoken
 *  clock time when there was one, otherwise an all-day event. Tasks with only a spoken phrase ("next week") are never guessed into a date. */
object CalendarSync {
    fun hasPermission(c: Context) = listOf(Manifest.permission.READ_CALENDAR, Manifest.permission.WRITE_CALENDAR)
        .all { ContextCompat.checkSelfPermission(c, it) == PackageManager.PERMISSION_GRANTED }

    private fun writableCalendarId(c: Context): Long? =
        c.contentResolver.query(
            CalendarContract.Calendars.CONTENT_URI, arrayOf(CalendarContract.Calendars._ID),
            "${CalendarContract.Calendars.CALENDAR_ACCESS_LEVEL} >= ? AND ${CalendarContract.Calendars.VISIBLE} = 1",
            arrayOf(CalendarContract.Calendars.CAL_ACCESS_CONTRIBUTOR.toString()), CalendarContract.Calendars.ACCOUNT_NAME,
        )?.use { if (it.moveToFirst()) it.getLong(0) else null }

    /** Returns how many events were created, or -1 if the phone has no writable calendar. Each task is marked, so calling this twice never duplicates. */
    suspend fun addDue(c: Context, dao: MemoryDao, tasks: List<ActionItem>): Int {
        val cal = writableCalendarId(c) ?: return -1
        var n = 0
        for (t in tasks.filter { it.dueDate != null && it.calendarEventId == null }) {
            val date = try { LocalDate.parse(t.dueDate) } catch (e: Exception) { continue }
            val time = t.dueTime?.let { runCatching { LocalTime.parse(it) }.getOrNull() }
            val zone = ZoneId.systemDefault()
            val (start, end) = time?.let { val s = date.atTime(it).atZone(zone).toInstant().toEpochMilli(); s to s + 30 * 60_000 } ?: allDayRange(date)
            val v = ContentValues().apply {
                put(CalendarContract.Events.CALENDAR_ID, cal)
                put(CalendarContract.Events.TITLE, t.title)
                put(CalendarContract.Events.DESCRIPTION, listOfNotNull("From a voice capture", t.assignee, t.description.ifBlank { null }).joinToString(" · "))
                put(CalendarContract.Events.DTSTART, start)
                put(CalendarContract.Events.DTEND, end)
                put(CalendarContract.Events.ALL_DAY, if (time == null) 1 else 0)
                put(CalendarContract.Events.EVENT_TIMEZONE, if (time == null) "UTC" else zone.id)
                put(CalendarContract.Events.HAS_ALARM, if (time == null) 0 else 1)
            }
            val uri = c.contentResolver.insert(CalendarContract.Events.CONTENT_URI, v) ?: continue
            if (time != null) c.contentResolver.insert(CalendarContract.Reminders.CONTENT_URI, ContentValues().apply {
                put(CalendarContract.Reminders.EVENT_ID, ContentUris.parseId(uri))
                put(CalendarContract.Reminders.MINUTES, 0)
                put(CalendarContract.Reminders.METHOD, CalendarContract.Reminders.METHOD_ALERT)
            })
            dao.setEvent(t.id, ContentUris.parseId(uri))
            n++
        }
        return n
    }
}
