package com.newsapiens.memory.mobile

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.rounded.ErrorOutline
import androidx.compose.material.icons.rounded.GraphicEq
import androidx.compose.material.icons.rounded.Schedule
import androidx.compose.material.icons.rounded.Sync
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import java.time.Instant
import java.time.LocalDate
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import java.time.format.FormatStyle

private val Light = lightColorScheme(
    primary = Color(0xFF6B4EF6), onPrimary = Color.White,
    primaryContainer = Color(0xFFEAE5FF), onPrimaryContainer = Color(0xFF221266),
    secondaryContainer = Color(0xFFDCD3FF), tertiaryContainer = Color(0xFFE3F4E6), onTertiaryContainer = Color(0xFF1B5E2A),
    background = Color(0xFFF8F7FC), onBackground = Color(0xFF111015),
    surface = Color.White, onSurface = Color(0xFF111015), onSurfaceVariant = Color(0xFF6E6A7C),
    surfaceVariant = Color(0xFFF0EEF6), outlineVariant = Color(0xFFE7E4EF), error = Color(0xFFE5484D),
)

private val Dark = darkColorScheme(
    primary = Color(0xFFA996FF), onPrimary = Color(0xFF1C1145),
    primaryContainer = Color(0xFF2B2450), onPrimaryContainer = Color(0xFFE6E0FF),
    secondaryContainer = Color(0xFF3A2F6B), tertiaryContainer = Color(0xFF1C3322), onTertiaryContainer = Color(0xFF9BE3AC),
    background = Color(0xFF0E0D12), onBackground = Color(0xFFF3F1F8),
    surface = Color(0xFF18171E), onSurface = Color(0xFFF3F1F8), onSurfaceVariant = Color(0xFFA6A2B4),
    surfaceVariant = Color(0xFF23212B), outlineVariant = Color(0xFF2C2A35), error = Color(0xFFFF6B6E),
)

/** Donut / legend colours, in rank order. */
val TopicColors = listOf(Color(0xFF6B4EF6), Color(0xFFB39DFF), Color(0xFF5B8DEF), Color(0xFFF06AA6), Color(0xFFC9C3D9))

@Composable
fun AppTheme(content: @Composable () -> Unit) =
    MaterialTheme(colorScheme = if (isSystemInDarkTheme()) Dark else Light, content = content)

private val fmt = DateTimeFormatter.ofLocalizedDateTime(FormatStyle.MEDIUM, FormatStyle.SHORT)
private val timeFmt = DateTimeFormatter.ofLocalizedTime(FormatStyle.SHORT)
fun whenText(ms: Long): String = fmt.format(Instant.ofEpochMilli(ms).atZone(ZoneId.systemDefault()))
fun timeText(ms: Long): String = timeFmt.format(Instant.ofEpochMilli(ms).atZone(ZoneId.systemDefault()))
fun dayOf(ms: Long): LocalDate = Instant.ofEpochMilli(ms).atZone(ZoneId.systemDefault()).toLocalDate()

/** "today" / "yesterday" / "3 Nov" for a timestamp. */
fun shortDay(ms: Long): String = when (val d = dayOf(ms)) {
    LocalDate.now() -> "today"; LocalDate.now().minusDays(1) -> "yesterday"; else -> DateTimeFormatter.ofPattern("d MMM").format(d)
}

/** "Today · 28 min" / "Yesterday · 4:10 PM" style meta line. */
fun shortWhen(c: Capture): String {
    val d = dayOf(c.startedAt)
    val today = LocalDate.now()
    val day = when (d) { today -> "Today"; today.minusDays(1) -> "Yesterday"; else -> DateTimeFormatter.ofPattern("d MMM").format(d) }
    return "$day · " + (minutes(c) ?: timeFmt.format(Instant.ofEpochMilli(c.startedAt).atZone(ZoneId.systemDefault())))
}

fun minutes(c: Capture): String? = c.durationS?.let { "${maxOf(1, Math.round(it / 60))} min" }

fun statusText(c: Capture) = when (c.status) {
    "queued" -> "Waiting to upload" + (c.error?.let { " — $it" } ?: "")
    "processing" -> "Transcribing and summarising…"
    else -> "Failed — ${c.error ?: "unknown error"}"
}

fun captureTitle(c: Capture) = if (c.status == "ready") c.title.ifBlank { titleOf(c.summary) } else "New capture"

/** "2026-11-03" -> "3 Nov"; anything unparseable is shown as-is. */
fun dueText(iso: String): String = try { DateTimeFormatter.ofPattern("d MMM").format(LocalDate.parse(iso)) } catch (e: Exception) { iso }

fun plural(n: Int, word: String) = "$n $word" + if (n == 1) "" else "s"

@Composable
fun CaptureRow(c: Capture, meta: String, onClick: () -> Unit) {
    val icon = when (c.status) { "ready" -> Icons.Rounded.GraphicEq; "processing" -> Icons.Rounded.Sync; "queued" -> Icons.Rounded.Schedule; else -> Icons.Rounded.ErrorOutline }
    Row(
        Modifier.fillMaxWidth().clip(RoundedCornerShape(14.dp)).clickable(onClick = onClick).padding(vertical = 10.dp, horizontal = 4.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Box(Modifier.size(38.dp).clip(CircleShape).background(MaterialTheme.colorScheme.primaryContainer), contentAlignment = Alignment.Center) {
            Icon(icon, null, Modifier.size(20.dp), tint = if (c.status == "failed") MaterialTheme.colorScheme.error else MaterialTheme.colorScheme.primary)
        }
        Spacer(Modifier.width(12.dp))
        Column(Modifier.weight(1f)) {
            Text(captureTitle(c), style = MaterialTheme.typography.titleSmall, fontWeight = FontWeight.SemiBold, maxLines = 1, overflow = TextOverflow.Ellipsis)
            Text(if (c.status == "ready") meta else statusText(c), style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant, maxLines = 1, overflow = TextOverflow.Ellipsis)
        }
    }
}

/** Pill segmented control (Day/Week/Month, Summary/Transcript, Open/Done/All). */
@Composable
fun Segmented(options: List<String>, selected: Int, onSelect: (Int) -> Unit, modifier: Modifier = Modifier) {
    val cs = MaterialTheme.colorScheme
    Row(modifier.clip(CircleShape).background(cs.surfaceVariant).padding(4.dp), horizontalArrangement = Arrangement.spacedBy(4.dp)) {
        options.forEachIndexed { i, label ->
            val on = i == selected
            Box(
                Modifier.weight(1f).clip(CircleShape).background(if (on) cs.onBackground else Color.Transparent)
                    .clickable { onSelect(i) }.padding(vertical = 9.dp),
                contentAlignment = Alignment.Center,
            ) {
                Text(label, style = MaterialTheme.typography.labelLarge, color = if (on) cs.background else cs.onSurfaceVariant, maxLines = 1)
            }
        }
    }
}

@Composable
fun SectionTitle(text: String, modifier: Modifier = Modifier) =
    Text(text, modifier, style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.Bold)
