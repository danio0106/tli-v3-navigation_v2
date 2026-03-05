import QtQuick 2.15

Rectangle {
    id: root
    color: "transparent"
    property var overlayData: ({})

    function refresh() {
        overlayCanvas.requestPaint()
    }

    Canvas {
        id: overlayCanvas
        anchors.fill: parent
        renderTarget: Canvas.Image
        antialiasing: true

        function _circle(ctx, x, y, r, fill, stroke, lineW) {
            if (x === undefined || y === undefined) {
                return
            }
            ctx.beginPath()
            ctx.arc(x, y, r, 0, Math.PI * 2)
            if (fill) {
                ctx.fillStyle = fill
                ctx.fill()
            }
            if (stroke) {
                ctx.lineWidth = lineW || 1
                ctx.strokeStyle = stroke
                ctx.stroke()
            }
        }

        function _polyline(ctx, points, color, lineW, dashed) {
            if (!points || points.length < 2) {
                return
            }
            ctx.beginPath()
            ctx.moveTo(points[0].x, points[0].y)
            for (var i = 1; i < points.length; i += 1) {
                ctx.lineTo(points[i].x, points[i].y)
            }
            ctx.strokeStyle = color
            ctx.lineWidth = lineW || 1
            ctx.setLineDash(dashed ? [6, 4] : [])
            ctx.stroke()
            ctx.setLineDash([])
        }

        function _polygon(ctx, points, fill, stroke, lineW) {
            if (!points || points.length < 3) {
                return
            }
            ctx.beginPath()
            ctx.moveTo(points[0].x, points[0].y)
            for (var i = 1; i < points.length; i += 1) {
                ctx.lineTo(points[i].x, points[i].y)
            }
            ctx.closePath()
            if (fill) {
                ctx.fillStyle = fill
                ctx.fill()
            }
            if (stroke) {
                ctx.strokeStyle = stroke
                ctx.lineWidth = lineW || 1
                ctx.stroke()
            }
        }

        function _label(ctx, text, x, y, color, font, align, baseline) {
            if (!text) {
                return
            }
            ctx.font = font || "bold 12px Consolas"
            ctx.textAlign = align || "left"
            ctx.textBaseline = baseline || "middle"
            // Dark halo for readability over bright in-game scenes.
            ctx.fillStyle = "rgba(0, 0, 0, 0.82)"
            ctx.fillText(text, x + 1, y + 1)
            ctx.fillStyle = color || "#C9D1D9"
            ctx.fillText(text, x, y)
        }

        onPaint: {
            var ctx = getContext("2d")
            ctx.reset()

            var data = root.overlayData || {}
            var layers = data.layers || {}
            var portals = data.portals || []
            var events = data.events || []
            var guards = data.guards || []
            var entities = data.entities || []
            var navCollision = data.navCollision || []
            var gridWalkable = data.gridWalkable || []
            var gridFrontier = data.gridFrontier || []
            var waypoints = data.waypoints || []
            var pathLines = data.pathLines || []
            var autoPath = data.autoPath || []
            var navLine = data.navLine || null
            var player = data.player || null
            var stuck = !!data.stuck

            if (layers.grid) {
                for (var gw = 0; gw < gridWalkable.length; gw += 1) {
                    var wp = gridWalkable[gw]
                    _circle(ctx, wp.x, wp.y, 1.4, "#1B4332", "", 0)
                }
                for (var gf = 0; gf < gridFrontier.length; gf += 1) {
                    var fp = gridFrontier[gf]
                    _circle(ctx, fp.x, fp.y, 1.8, "#39D353", "", 0)
                }
            }

            if (layers.waypoints) {
                for (var p = 0; p < pathLines.length; p += 1) {
                    _polyline(ctx, [pathLines[p].a, pathLines[p].b], "#8B949E", 1, false)
                }
            }

            if (layers.auto_path && autoPath.length >= 2) {
                _polyline(ctx, autoPath, "#39D353", 2, true)
            }

            if (layers.nav_target && navLine && navLine.from && navLine.to) {
                _polyline(ctx, [navLine.from, navLine.to], "#D2A8FF", 2, false)
            }

            if (layers.nav_collision) {
                for (var n = 0; n < navCollision.length; n += 1) {
                    var nc = navCollision[n]
                    var style = nc.style || "raw"
                    var stroke = "#7EE787"
                    var fill = "rgba(126, 231, 135, 0.06)"
                    if (style === "inflated") {
                        stroke = "#F2CC60"
                        fill = "rgba(242, 204, 96, 0.07)"
                    } else if (style === "bridge") {
                        stroke = "#58A6FF"
                        fill = "rgba(88, 166, 255, 0.08)"
                    }
                    _polygon(ctx, nc.points || [], fill, stroke, 1.4)
                }
            }

            if (layers.portals) {
                for (var i = 0; i < portals.length; i += 1) {
                    var portal = portals[i]
                    var color = portal.isExit ? "#58A6FF" : "#FF7B72"
                    _circle(ctx, portal.x, portal.y, portal.isExit ? 7 : 6, "rgba(0,0,0,0)", color, 2)
                    _label(ctx, portal.label || "", portal.x + 12, portal.y, color, "bold 12px Consolas", "left", "middle")
                }
            }

            if (layers.events) {
                for (var j = 0; j < events.length; j += 1) {
                    var eventMarker = events[j]
                    var eventColor = "#FFA657"
                    if (eventMarker.type === "Carjack") {
                        eventColor = "#FF7B72"
                    } else if (eventMarker.type === "Sandlord") {
                        eventColor = "#FFD866"
                    }
                    _circle(ctx, eventMarker.x, eventMarker.y, 7, "rgba(0,0,0,0)", eventColor, 2)
                    _label(ctx, eventMarker.label || eventMarker.type || "", eventMarker.x + 12, eventMarker.y, eventColor, "bold 12px Consolas", "left", "middle")
                }
            }

            if (layers.entities) {
                for (var en = 0; en < entities.length; en += 1) {
                    var ent = entities[en]
                    _circle(ctx, ent.x, ent.y, 4, "rgba(255, 155, 155, 0.12)", "#FF9B9B", 1.2)
                    if (ent.name) {
                        _label(ctx, ent.name, ent.x + 8, ent.y, "#FF9B9B", "11px Consolas", "left", "middle")
                    }
                }
            }

            if (layers.events) {
                for (var g = 0; g < guards.length; g += 1) {
                    var guard = guards[g]
                    _circle(ctx, guard.x, guard.y, 6, "rgba(0,0,0,0)", "#FF8C00", 2)
                    _label(ctx, guard.label || "", guard.x + 10, guard.y, "#FF8C00", "bold 12px Consolas", "left", "middle")
                }
            }

            if (layers.waypoints) {
                for (var w = 0; w < waypoints.length; w += 1) {
                    var wp2 = waypoints[w]
                    var wpColor = wp2.kind === "stand" ? "#FFD866" : (wp2.isPortal ? "#FF7B72" : "#79C0FF")
                    var wpRadius = wp2.current ? 6 : 4
                    _circle(ctx, wp2.x, wp2.y, wpRadius, wp2.current ? "#FFFFFF" : "rgba(0,0,0,0)", wpColor, 2)
                    _label(ctx, wp2.label || "", wp2.x + 8, wp2.y - 8, wpColor, "11px Consolas", "left", "middle")
                }
            }

            if (layers.player && player) {
                _circle(ctx, player.x, player.y, 8, "#7EE787", "#FFFFFF", 2)
                if (player.label) {
                    _label(ctx, player.label, player.x, player.y + 15, "#7EE787", "bold 16px Consolas", "center", "top")
                }
            }

            if (layers.minimap && player) {
                var panelSize = 186
                var margin = 14
                var x0 = width - panelSize - margin
                var y0 = height - panelSize - margin
                var x1 = width - margin
                var y1 = height - margin
                var cx = (x0 + x1) * 0.5
                var cy = (y0 + y1) * 0.5

                ctx.fillStyle = "rgba(13, 17, 23, 0.78)"
                ctx.strokeStyle = "#30363D"
                ctx.lineWidth = 1
                ctx.beginPath()
                ctx.rect(x0, y0, panelSize, panelSize)
                ctx.fill()
                ctx.stroke()

                var range = 2000.0
                function _mxy(sx, sy) {
                    return {
                        x: cx + ((sx - player.x) / range) * panelSize * 0.5,
                        y: cy + ((sy - player.y) / range) * panelSize * 0.5
                    }
                }

                _circle(ctx, cx, cy, panelSize * 0.18, "", "#21262D", 1)
                _circle(ctx, cx, cy, panelSize * 0.38, "", "#21262D", 1)

                if (waypoints.length > 1) {
                    for (var wl = 1; wl < waypoints.length; wl += 1) {
                        var wa = _mxy(waypoints[wl - 1].x, waypoints[wl - 1].y)
                        var wb = _mxy(waypoints[wl].x, waypoints[wl].y)
                        _polyline(ctx, [wa, wb], "#8B949E", 1, false)
                    }
                }

                for (var mp = 0; mp < portals.length; mp += 1) {
                    var pp = _mxy(portals[mp].x, portals[mp].y)
                    var pcol = portals[mp].isExit ? "#58A6FF" : "#FF7B72"
                    _circle(ctx, pp.x, pp.y, 3.5, "rgba(0,0,0,0)", pcol, 1)
                }

                for (var me = 0; me < events.length; me += 1) {
                    var ev = events[me]
                    var ep = _mxy(ev.x, ev.y)
                    var ecol = ev.type === "Carjack" ? "#FF7B72" : (ev.type === "Sandlord" ? "#FFD866" : "#6E7681")
                    _circle(ctx, ep.x, ep.y, 3.5, "rgba(0,0,0,0)", ecol, 1)
                }

                _circle(ctx, cx, cy, 4, "#7EE787", "#7EE787", 1)
                _label(ctx, "N", cx, y0 + 8, "#30363D", "10px Consolas", "center", "top")
            }

            if (layers.stuck && stuck) {
                var boxW = 136
                var boxH = 26
                var x = (width - boxW) * 0.5
                var y = 16
                ctx.fillStyle = "rgba(255, 123, 114, 0.22)"
                ctx.strokeStyle = "#FF7B72"
                ctx.lineWidth = 1.4
                ctx.beginPath()
                ctx.rect(x, y, boxW, boxH)
                ctx.fill()
                ctx.stroke()
                ctx.font = "bold 12px Segoe UI"
                ctx.fillStyle = "#FF7B72"
                ctx.textAlign = "center"
                ctx.textBaseline = "middle"
                ctx.fillText("STUCK", x + boxW / 2, y + boxH / 2)
            }

            // Keep a tiny status tag so overlay visibility is obvious even
            // when scanner markers are temporarily unavailable.
            _label(ctx, "Overlay ON", 12, 12, "#7EE787", "bold 11px Consolas", "left", "top")
        }
    }
}
