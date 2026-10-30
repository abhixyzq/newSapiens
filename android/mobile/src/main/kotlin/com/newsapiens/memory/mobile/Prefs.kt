package com.newsapiens.memory.mobile

import android.content.Context
import java.io.File

/** Backend URL + API token, in app-private storage. */
object Prefs {
    private fun sp(c: Context) = c.getSharedPreferences("settings", Context.MODE_PRIVATE)
    fun baseUrl(c: Context) = sp(c).getString("url", "")!!.trim().trimEnd('/')
    fun token(c: Context) = sp(c).getString("token", "")!!.trim()
    fun save(c: Context, url: String, token: String) = sp(c).edit().putString("url", url).putString("token", token).apply()
}

object Files {
    fun audio(c: Context, id: Long) = File(File(c.filesDir, "inbox").apply { mkdirs() }, "$id.m4a")
}
