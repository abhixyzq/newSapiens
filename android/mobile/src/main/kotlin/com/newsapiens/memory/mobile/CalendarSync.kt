package com.newsapiens.memory.mobile

import android.Manifest
import android.content.ContentUris
import android.content.ContentValues
import android.content.Context
import android.content.pm.PackageManager
import android.provider.CalendarContract
import androidx.core.content.ContextCompat
import java.time.LocalDate

/** Pushes tasks that have a certain due date to the device calendar as all-day events.
 *  Tasks with only a spoken phrase ("next week") are never guessed into a date. */
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
    suspend fun addDue(c: Context, dao: MemoryDao, captureId: Long): Int {
        val cal = writableCalendarId(c) ?: return -1
        var n = 0
        for (t in dao.unsyncedDue(captureId)) {
            val date = try { LocalDate.parse(t.dueDate) } catch (e: Exception) { continue }
            val (start, end) = allDayRange(date)
            val v = ContentValues().apply {
                put(CalendarContract.Events.CALENDAR_ID, cal)
                put(CalendarContract.Events.TITLE, t.title)
                put(CalendarContract.Events.DESCRIPTION, "From a voice capture" + (t.assignee?.let { " · $it" } ?: ""))
                put(CalendarContract.Events.DTSTART, start)
                put(CalendarContract.Events.DTEND, end)
                put(CalendarContract.Events.ALL_DAY, 1)
                put(CalendarContract.Events.EVENT_TIMEZONE, "UTC")
            }
            val uri = c.contentResolver.insert(CalendarContract.Events.CONTENT_URI, v) ?: continue
            dao.setEvent(t.id, ContentUris.parseId(uri))
            n++
        }
        return n
    }
}
