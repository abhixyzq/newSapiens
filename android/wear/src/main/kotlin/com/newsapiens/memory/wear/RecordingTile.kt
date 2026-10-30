package com.newsapiens.memory.wear

import androidx.wear.protolayout.ActionBuilders
import androidx.wear.protolayout.ColorBuilders.argb
import androidx.wear.protolayout.ModifiersBuilders
import androidx.wear.protolayout.ResourceBuilders
import androidx.wear.protolayout.TimelineBuilders
import androidx.wear.protolayout.material.Colors
import androidx.wear.protolayout.material.CompactChip
import androidx.wear.protolayout.material.Text
import androidx.wear.protolayout.material.Typography
import androidx.wear.protolayout.material.layouts.PrimaryLayout
import androidx.wear.tiles.RequestBuilders
import androidx.wear.tiles.TileBuilders
import androidx.wear.tiles.TileService
import com.google.common.util.concurrent.Futures
import com.google.common.util.concurrent.ListenableFuture

/** One-tap start/stop. The chip launches MainActivity with toggle=true (tiles cannot start services directly). */
class RecordingTile : TileService() {
    override fun onTileRequest(req: RequestBuilders.TileRequest): ListenableFuture<TileBuilders.Tile> {
        val rec = Recorder.state.value.recording
        val click = ModifiersBuilders.Clickable.Builder().setId("toggle").setOnClick(
            ActionBuilders.LaunchAction.Builder().setAndroidActivity(
                ActionBuilders.AndroidActivity.Builder().setPackageName(packageName)
                    .setClassName(MainActivity::class.java.name)
                    .addKeyToExtraMapping("toggle", ActionBuilders.booleanExtra(true)).build()
            ).build()
        ).build()
        val cfg = req.deviceConfiguration
        val layout = PrimaryLayout.Builder(cfg)
            .setPrimaryLabelTextContent(
                Text.Builder(this, if (rec) "Recording" else "Voice memory")
                    .setTypography(Typography.TYPOGRAPHY_TITLE3).setColor(argb(Colors.DEFAULT.onSurface)).build()
            )
            .setContent(CompactChip.Builder(this, if (rec) "Stop" else "Record", click, cfg).build())
            .build()
        return Futures.immediateFuture(
            TileBuilders.Tile.Builder().setResourcesVersion("1")
                .setTileTimeline(TimelineBuilders.Timeline.fromLayoutElement(layout)).build()
        )
    }

    override fun onTileResourcesRequest(req: RequestBuilders.ResourcesRequest): ListenableFuture<ResourceBuilders.Resources> =
        Futures.immediateFuture(ResourceBuilders.Resources.Builder().setVersion("1").build())
}
