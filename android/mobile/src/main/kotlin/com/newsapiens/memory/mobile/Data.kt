package com.newsapiens.memory.mobile

import android.content.Context
import androidx.room.ColumnInfo
import androidx.room.Dao
import androidx.room.Database
import androidx.room.Entity
import androidx.room.Fts4
import androidx.room.Insert
import androidx.room.OnConflictStrategy
import androidx.room.PrimaryKey
import androidx.room.Query
import androidx.room.Room
import androidx.room.RoomDatabase
import androidx.room.Transaction
import androidx.room.Update
import androidx.room.migration.Migration
import androidx.sqlite.db.SupportSQLiteDatabase
import kotlinx.coroutines.flow.Flow

/** id = start time (epoch ms) of the recording; it is also the audio file name and the de-dupe key. */
@Entity
data class Capture(
    @PrimaryKey val id: Long,
    val startedAt: Long,
    val status: String,  // queued | processing | ready | failed
    val error: String? = null,
    val durationS: Double? = null,
    val language: String? = null,
    val summary: String = "",
    val topics: String = "",
    val transcript: String = "",
)

@Entity
data class Decision(@PrimaryKey(autoGenerate = true) val id: Long = 0, val captureId: Long, val text: String)

@Entity
data class ActionItem(
    @PrimaryKey(autoGenerate = true) val id: Long = 0,
    val captureId: Long,
    val title: String,
    val assignee: String?,
    val dueDate: String?,  // ISO yyyy-MM-dd, only when the extractor was sure
    val dueText: String?,  // the spoken phrase ("next week") when it was not
    val priority: String,
    @ColumnInfo(defaultValue = "NULL") val calendarEventId: Long? = null,
    @ColumnInfo(defaultValue = "0") val done: Boolean = false,
)

@Fts4(contentEntity = Capture::class)
@Entity
data class CaptureFts(val summary: String, val transcript: String)

@Dao
abstract class MemoryDao {
    @Insert(onConflict = OnConflictStrategy.IGNORE) abstract suspend fun insertCapture(c: Capture): Long
    @Update abstract suspend fun updateCapture(c: Capture)
    @Insert abstract suspend fun insertDecisions(d: List<Decision>)
    @Insert abstract suspend fun insertTasks(t: List<ActionItem>)

    @Query("SELECT * FROM Capture ORDER BY startedAt DESC") abstract fun captures(): Flow<List<Capture>>
    @Query("SELECT * FROM Capture WHERE id = :id") abstract fun captureFlow(id: Long): Flow<Capture?>
    @Query("SELECT * FROM Capture WHERE id = :id") abstract suspend fun capture(id: Long): Capture?
    @Query("SELECT * FROM Decision WHERE captureId = :id") abstract fun decisions(id: Long): Flow<List<Decision>>
    @Query("SELECT * FROM ActionItem WHERE captureId = :id ORDER BY id") abstract fun tasks(id: Long): Flow<List<ActionItem>>
    @Query("SELECT * FROM ActionItem WHERE captureId = :id AND dueDate IS NOT NULL AND calendarEventId IS NULL")
    abstract suspend fun unsyncedDue(id: Long): List<ActionItem>
    @Query("UPDATE ActionItem SET calendarEventId = :event WHERE id = :id") abstract suspend fun setEvent(id: Long, event: Long)
    @Query("SELECT * FROM ActionItem ORDER BY id DESC") abstract fun allTasks(): Flow<List<ActionItem>>
    @Query("SELECT * FROM Decision") abstract fun allDecisions(): Flow<List<Decision>>
    @Query("UPDATE ActionItem SET done = :done WHERE id = :id") abstract suspend fun setDone(id: Long, done: Boolean)

    @Query("SELECT Capture.* FROM Capture JOIN CaptureFts ON Capture.rowid = CaptureFts.rowid WHERE CaptureFts MATCH :q ORDER BY startedAt DESC")
    abstract suspend fun search(q: String): List<Capture>

    @Query("DELETE FROM Decision WHERE captureId = :id") abstract suspend fun delDecisions(id: Long)
    @Query("DELETE FROM ActionItem WHERE captureId = :id") abstract suspend fun delTasks(id: Long)
    @Query("DELETE FROM Capture WHERE id = :id") abstract suspend fun delCapture(id: Long)

    @Transaction open suspend fun saveResult(c: Capture, d: List<Decision>, t: List<ActionItem>) {
        delDecisions(c.id); delTasks(c.id); updateCapture(c); insertDecisions(d); insertTasks(t)
    }

    @Transaction open suspend fun deleteCapture(id: Long) { delDecisions(id); delTasks(id); delCapture(id) }
}

@Database(entities = [Capture::class, Decision::class, ActionItem::class, CaptureFts::class], version = 2, exportSchema = false)
abstract class Db : RoomDatabase() {
    abstract fun dao(): MemoryDao

    companion object {
        @Volatile private var inst: Db? = null
        private val v2 = object : Migration(1, 2) {
            override fun migrate(db: SupportSQLiteDatabase) = db.execSQL("ALTER TABLE ActionItem ADD COLUMN done INTEGER NOT NULL DEFAULT 0")
        }
        fun get(ctx: Context): Db = inst ?: synchronized(this) {
            inst ?: Room.databaseBuilder(ctx.applicationContext, Db::class.java, "memory.db").addMigrations(v2).build().also { inst = it }
        }
    }
}
