package com.newsapiens.memory.mobile

import android.content.Context
import androidx.room.ColumnInfo
import androidx.room.Dao
import androidx.room.Database
import androidx.room.Entity
import androidx.room.ForeignKey
import androidx.room.Fts4
import androidx.room.Index
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
    @ColumnInfo(defaultValue = "''") val title: String = "",
    val processedAt: Long? = null,  // when the server analysed it; never used to place anything on the timeline
    val speechS: Double? = null,  // seconds of actual speech (sum of segments), not recording length
    @ColumnInfo(defaultValue = "''") val sourceTranscript: String = "",  // original language; empty = same as transcript
)

/** Important moment: a decision, task, commitment, idea or risk, at the speech time it was said (capture start + segment offset).
 *  line = transcript line (segment index) it cites, so the UI can open the transcript there. */
@Entity(
    foreignKeys = [ForeignKey(Capture::class, ["id"], ["captureId"], onDelete = ForeignKey.CASCADE)],
    indices = [Index("captureId"), Index("atMs")],
)
data class Moment(
    @PrimaryKey(autoGenerate = true) val id: Long = 0,
    val captureId: Long,
    val category: String,  // decision | reminder | commitment | idea | work
    val title: String,
    val text: String,
    val atMs: Long,
    val importance: Double,
    val line: Int?,
)

/** One activity in a capture, grouped by the server from consecutive transcript lines; times are speech time. */
@Entity(
    foreignKeys = [ForeignKey(Capture::class, ["id"], ["captureId"], onDelete = ForeignKey.CASCADE)],
    indices = [Index("captureId"), Index("startMs")],
)
data class Event(
    @PrimaryKey(autoGenerate = true) val id: Long = 0,
    val captureId: Long,
    val title: String,
    val description: String,
    val category: String,  // work | study | personal | meeting | other
    val startMs: Long,
    val endMs: Long,
    val importance: Double,
    val line: Int?,
)

/** "Your day at a glance" for one local date; generatedAt is processing time. */
@Entity
data class DailySummary(@PrimaryKey val date: String, val summary: String, val highlights: String, val sourceCount: Int, val generatedAt: Long)

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
    @ColumnInfo(defaultValue = "''") val description: String = "",
    val atMs: Long? = null,  // speech time it was said
    val line: Int? = null,
    val dueTime: String? = null,  // "HH:mm" when a clock time was said; the calendar reminder fires then
)

@Fts4(contentEntity = Capture::class)
@Entity
data class CaptureFts(val summary: String, val transcript: String)

@Dao
abstract class MemoryDao {
    @Insert(onConflict = OnConflictStrategy.IGNORE) abstract suspend fun insertCapture(c: Capture): Long
    @Update abstract suspend fun updateCapture(c: Capture)
    @Insert abstract suspend fun insertMoments(m: List<Moment>)
    @Insert abstract suspend fun insertEvents(e: List<Event>)
    @Insert abstract suspend fun insertTasks(t: List<ActionItem>)
    @Update abstract suspend fun updateTask(t: ActionItem)
    @Query("DELETE FROM ActionItem WHERE id = :id") abstract suspend fun deleteTask(id: Long)
    @Insert(onConflict = OnConflictStrategy.REPLACE) abstract suspend fun saveSummary(s: DailySummary)

    @Query("SELECT * FROM Capture ORDER BY startedAt DESC") abstract fun captures(): Flow<List<Capture>>
    @Query("SELECT * FROM Capture WHERE id = :id") abstract fun captureFlow(id: Long): Flow<Capture?>
    @Query("SELECT * FROM Capture WHERE id = :id") abstract suspend fun capture(id: Long): Capture?
    @Query("SELECT * FROM Moment WHERE captureId = :id AND category = 'decision' ORDER BY atMs") abstract fun decisions(id: Long): Flow<List<Moment>>
    @Query("SELECT * FROM ActionItem WHERE captureId = :id ORDER BY id") abstract fun tasks(id: Long): Flow<List<ActionItem>>
    @Query("SELECT * FROM ActionItem WHERE captureId = :id AND dueDate IS NOT NULL AND calendarEventId IS NULL")
    abstract suspend fun unsyncedDue(id: Long): List<ActionItem>
    @Query("UPDATE ActionItem SET calendarEventId = :event WHERE id = :id") abstract suspend fun setEvent(id: Long, event: Long)
    @Query("SELECT * FROM ActionItem ORDER BY id DESC") abstract fun allTasks(): Flow<List<ActionItem>>
    @Query("SELECT * FROM Moment WHERE category = 'decision'") abstract fun allDecisions(): Flow<List<Moment>>

    // one local day = [from, to) in epoch ms of speech time
    @Query("SELECT * FROM Capture WHERE startedAt >= :from AND startedAt < :to ORDER BY startedAt") abstract fun capturesBetween(from: Long, to: Long): Flow<List<Capture>>
    @Query("SELECT * FROM Moment WHERE atMs >= :from AND atMs < :to ORDER BY atMs") abstract fun momentsBetween(from: Long, to: Long): Flow<List<Moment>>
    @Query("SELECT * FROM Event WHERE startMs >= :from AND startMs < :to ORDER BY startMs") abstract fun eventsBetween(from: Long, to: Long): Flow<List<Event>>
    @Query("SELECT * FROM DailySummary WHERE date = :date") abstract fun summary(date: String): Flow<DailySummary?>
    @Query("SELECT * FROM DailySummary WHERE date = :date") abstract suspend fun summaryNow(date: String): DailySummary?
    @Query("UPDATE ActionItem SET done = :done WHERE id = :id") abstract suspend fun setDone(id: Long, done: Boolean)

    @Query("SELECT Capture.* FROM Capture JOIN CaptureFts ON Capture.rowid = CaptureFts.rowid WHERE CaptureFts MATCH :q ORDER BY startedAt DESC")
    abstract suspend fun search(q: String): List<Capture>

    @Query("DELETE FROM Moment WHERE captureId = :id") abstract suspend fun delMoments(id: Long)
    @Query("DELETE FROM Event WHERE captureId = :id") abstract suspend fun delEvents(id: Long)
    @Query("DELETE FROM ActionItem WHERE captureId = :id") abstract suspend fun delTasks(id: Long)
    @Query("DELETE FROM Capture WHERE id = :id") abstract suspend fun delCapture(id: Long)

    @Transaction open suspend fun saveResult(c: Capture, m: List<Moment>, e: List<Event>, t: List<ActionItem>) {
        delMoments(c.id); delEvents(c.id); delTasks(c.id); updateCapture(c); insertMoments(m); insertEvents(e); insertTasks(t)
    }

    @Transaction open suspend fun deleteCapture(id: Long) { delMoments(id); delEvents(id); delTasks(id); delCapture(id) }
}

@Database(entities = [Capture::class, Moment::class, Event::class, ActionItem::class, DailySummary::class, CaptureFts::class], version = 5, exportSchema = false)
abstract class Db : RoomDatabase() {
    abstract fun dao(): MemoryDao

    companion object {
        @Volatile private var inst: Db? = null
        private val v2 = object : Migration(1, 2) {
            override fun migrate(db: SupportSQLiteDatabase) = db.execSQL("ALTER TABLE ActionItem ADD COLUMN done INTEGER NOT NULL DEFAULT 0")
        }
        /** Decisions become moments (at the capture start: their offset was never stored); events and day summaries start empty. */
        private val v3 = object : Migration(2, 3) {
            override fun migrate(db: SupportSQLiteDatabase) = listOf(
                "ALTER TABLE Capture ADD COLUMN `title` TEXT NOT NULL DEFAULT ''",
                "ALTER TABLE Capture ADD COLUMN `processedAt` INTEGER",
                "ALTER TABLE Capture ADD COLUMN `speechS` REAL",
                "ALTER TABLE ActionItem ADD COLUMN `description` TEXT NOT NULL DEFAULT ''",
                "ALTER TABLE ActionItem ADD COLUMN `atMs` INTEGER",
                "ALTER TABLE ActionItem ADD COLUMN `line` INTEGER",
                "CREATE TABLE IF NOT EXISTS `Moment` (`id` INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL, `captureId` INTEGER NOT NULL, `category` TEXT NOT NULL, `title` TEXT NOT NULL, `text` TEXT NOT NULL, `atMs` INTEGER NOT NULL, `importance` REAL NOT NULL, `line` INTEGER, FOREIGN KEY(`captureId`) REFERENCES `Capture`(`id`) ON UPDATE NO ACTION ON DELETE CASCADE )",
                "CREATE INDEX IF NOT EXISTS `index_Moment_captureId` ON `Moment` (`captureId`)",
                "CREATE INDEX IF NOT EXISTS `index_Moment_atMs` ON `Moment` (`atMs`)",
                "CREATE TABLE IF NOT EXISTS `Event` (`id` INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL, `captureId` INTEGER NOT NULL, `title` TEXT NOT NULL, `description` TEXT NOT NULL, `category` TEXT NOT NULL, `startMs` INTEGER NOT NULL, `endMs` INTEGER NOT NULL, `importance` REAL NOT NULL, `line` INTEGER, FOREIGN KEY(`captureId`) REFERENCES `Capture`(`id`) ON UPDATE NO ACTION ON DELETE CASCADE )",
                "CREATE INDEX IF NOT EXISTS `index_Event_captureId` ON `Event` (`captureId`)",
                "CREATE INDEX IF NOT EXISTS `index_Event_startMs` ON `Event` (`startMs`)",
                "CREATE TABLE IF NOT EXISTS `DailySummary` (`date` TEXT NOT NULL, `summary` TEXT NOT NULL, `highlights` TEXT NOT NULL, `sourceCount` INTEGER NOT NULL, `generatedAt` INTEGER NOT NULL, PRIMARY KEY(`date`))",
                "INSERT INTO Moment (captureId, category, title, text, atMs, importance, line) " +
                    "SELECT d.captureId, 'decision', 'Decision', d.text, c.startedAt, 0.7, NULL FROM Decision d JOIN Capture c ON c.id = d.captureId",
                "UPDATE ActionItem SET atMs = (SELECT startedAt FROM Capture WHERE Capture.id = ActionItem.captureId)",
                "DROP TABLE Decision",
            ).forEach(db::execSQL)
        }
        private val v4 = object : Migration(3, 4) {
            override fun migrate(db: SupportSQLiteDatabase) = db.execSQL("ALTER TABLE ActionItem ADD COLUMN `dueTime` TEXT")
        }
        private val v5 = object : Migration(4, 5) {
            override fun migrate(db: SupportSQLiteDatabase) = db.execSQL("ALTER TABLE Capture ADD COLUMN `sourceTranscript` TEXT NOT NULL DEFAULT ''")
        }
        fun get(ctx: Context): Db = inst ?: synchronized(this) {
            inst ?: Room.databaseBuilder(ctx.applicationContext, Db::class.java, "memory.db").addMigrations(v2, v3, v4, v5).build().also { inst = it }
        }
    }
}
