package com.newsapiens.memory.mobile

import android.content.Context
import java.io.File

/** User name, in app-private storage. Backend URL is fixed at build time (BuildConfig.BACKEND_URL). */
object Prefs {
    private fun sp(c: Context) = c.getSharedPreferences("settings", Context.MODE_PRIVATE)
    fun name(c: Context) = sp(c).getString("name", "")!!.trim()
    fun save(c: Context, name: String) = sp(c).edit().putString("name", name).apply()
}

object Files {
    fun audio(c: Context, id: Long) = File(File(c.filesDir, "inbox").apply { mkdirs() }, "$id.m4a")
}
