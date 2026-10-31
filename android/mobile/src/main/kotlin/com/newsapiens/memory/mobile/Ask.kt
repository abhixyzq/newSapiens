package com.newsapiens.memory.mobile

import android.content.Context
import androidx.compose.foundation.background
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
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.rounded.ArrowBack
import androidx.compose.material.icons.automirrored.rounded.Send
import androidx.compose.material.icons.rounded.AutoAwesome
import androidx.compose.material.icons.rounded.DeleteOutline
import androidx.compose.material3.CenterAlignedTopAppBar
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.OutlinedTextFieldDefaults
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.runtime.snapshots.SnapshotStateList
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import java.io.IOException
import java.time.LocalDate
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONArray
import org.json.JSONException
import org.json.JSONObject

/** One chat line; role is "user" | "assistant", error lines are shown but never sent back as history. */
data class AskMsg(val role: String, val text: String, val error: Boolean = false)

/** Sends the question plus this phone's memories (they only live here) to POST /api/ask and returns the answer. */
suspend fun ask(ctx: Context, question: String, history: List<AskMsg>): String = withContext(Dispatchers.IO) {
    val dao = Db.get(ctx).dao()
    val dec = dao.allDecisions().first().groupBy { it.captureId }
    val tasks = dao.allTasks().first().groupBy { it.captureId }
    val memories = dao.captures().first().filter { it.status == "ready" }.take(50)
        .map { c -> memoryText(c, dec[c.id].orEmpty().map { it.text }, tasks[c.id].orEmpty().map { it.title }) }
    val body = JSONObject().put("question", question).put("today", LocalDate.now().toString())
        .put("memories", JSONArray(memories))
        .put("history", JSONArray(history.filter { !it.error }.takeLast(6).map { JSONObject().put("role", it.role).put("content", it.text) }))
    val req = Request.Builder().url("${BuildConfig.BACKEND_URL}/api/ask")
        .post(body.toString().toRequestBody("application/json".toMediaType())).build()
    ProcessWorker.client.newCall(req).execute().use { r ->
        val text = r.body?.string() ?: ""
        when {
            r.isSuccessful -> try { JSONObject(text).getString("answer") } catch (e: JSONException) { throw IOException("Unreadable server response") }
            r.code == 503 || r.code == 429 -> throw IOException("The AI is busy right now. Try again in a minute.")
            else -> throw IOException("Server said ${r.code}")
        }
    }
}

private val suggestions = listOf("What did I decide this week?", "What are my open tasks?", "Summarize my last capture")

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun AskScreen(chat: SnapshotStateList<AskMsg>, onBack: () -> Unit) {
    val ctx = LocalContext.current
    val scope = rememberCoroutineScope()
    var input by remember { mutableStateOf("") }
    var busy by remember { mutableStateOf(false) }
    val list = rememberLazyListState()
    val cs = MaterialTheme.colorScheme
    LaunchedEffect(chat.size, busy) { if (chat.isNotEmpty()) list.animateScrollToItem(chat.size - if (busy) 0 else 1) }

    fun send(q: String) {
        val question = q.trim()
        if (question.isEmpty() || busy) return
        val history = chat.toList()
        chat += AskMsg("user", question)
        input = ""
        busy = true
        scope.launch {
            chat += try { AskMsg("assistant", ask(ctx, question, history)) }
            catch (e: IOException) { AskMsg("assistant", e.message ?: "Couldn't reach the server", error = true) }
            busy = false
        }
    }

    Scaffold(
        containerColor = cs.background,
        topBar = {
            CenterAlignedTopAppBar(
                title = { Text("Ask", fontWeight = FontWeight.SemiBold) },
                navigationIcon = { IconButton(onBack) { Icon(Icons.AutoMirrored.Rounded.ArrowBack, "Back") } },
                actions = { if (chat.isNotEmpty() && !busy) IconButton({ chat.clear() }) { Icon(Icons.Rounded.DeleteOutline, "Clear chat") } },
                colors = TopAppBarDefaults.centerAlignedTopAppBarColors(containerColor = cs.background),
            )
        },
    ) { pad ->
        Column(Modifier.padding(pad).fillMaxSize().imePadding()) {
            if (chat.isEmpty()) {
                Column(Modifier.weight(1f).fillMaxWidth().padding(24.dp), verticalArrangement = Arrangement.Center, horizontalAlignment = Alignment.CenterHorizontally) {
                    Box(Modifier.size(64.dp).clip(CircleShape).background(cs.primaryContainer), contentAlignment = Alignment.Center) {
                        Icon(Icons.Rounded.AutoAwesome, null, Modifier.size(30.dp), tint = cs.primary)
                    }
                    Spacer(Modifier.height(14.dp))
                    Text("Ask your memory", style = MaterialTheme.typography.titleLarge, fontWeight = FontWeight.Bold)
                    Text("Questions about your captures, or anything else.", style = MaterialTheme.typography.bodyMedium,
                        color = cs.onSurfaceVariant, textAlign = TextAlign.Center)
                    Spacer(Modifier.height(20.dp))
                    suggestions.forEach {
                        Text(it, Modifier.padding(vertical = 4.dp).clip(CircleShape).background(cs.surface).clickable { send(it) }
                            .padding(horizontal = 16.dp, vertical = 10.dp), style = MaterialTheme.typography.bodyMedium)
                    }
                }
            } else {
                LazyColumn(Modifier.weight(1f).fillMaxWidth(), list, PaddingValues(16.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
                    itemsIndexed(chat) { _, m -> Bubble(m) }
                    if (busy) item { Bubble(AskMsg("assistant", "Thinking…")) }
                }
            }
            Row(Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 10.dp), verticalAlignment = Alignment.CenterVertically) {
                OutlinedTextField(
                    input, { input = it }, Modifier.weight(1f), placeholder = { Text("Ask anything…") }, maxLines = 4,
                    shape = RoundedCornerShape(24.dp),
                    colors = OutlinedTextFieldDefaults.colors(unfocusedContainerColor = cs.surface, focusedContainerColor = cs.surface, unfocusedBorderColor = cs.outlineVariant),
                )
                Spacer(Modifier.size(8.dp))
                IconButton({ send(input) }, Modifier.size(52.dp).clip(CircleShape).background(if (input.isBlank() || busy) cs.surfaceVariant else cs.onBackground),
                    enabled = input.isNotBlank() && !busy) {
                    Icon(Icons.AutoMirrored.Rounded.Send, "Send", tint = if (input.isBlank() || busy) cs.onSurfaceVariant else cs.background)
                }
            }
        }
    }
}

@Composable
private fun Bubble(m: AskMsg) {
    val cs = MaterialTheme.colorScheme
    val mine = m.role == "user"
    Box(Modifier.fillMaxWidth(), contentAlignment = if (mine) Alignment.CenterEnd else Alignment.CenterStart) {
        Text(
            m.text,
            Modifier.widthIn(max = 300.dp).clip(RoundedCornerShape(18.dp))
                .background(when { mine -> cs.onBackground; m.error -> cs.errorContainer; else -> cs.surface }).padding(horizontal = 14.dp, vertical = 10.dp),
            color = when { mine -> cs.background; m.error -> cs.onErrorContainer; else -> cs.onSurface },
            style = MaterialTheme.typography.bodyMedium,
        )
    }
}
