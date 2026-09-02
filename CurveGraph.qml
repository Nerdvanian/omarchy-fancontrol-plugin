import QtQuick
import qs.Commons

// Draggable temp/percent curve editor, styled after the graph in the
// Windows "Fan Control" app: a grid, a clamped-linear curve line, small
// draggable point handles, and a live marker for the current sensor
// reading. One MouseArea drives dragging (idiom matches PanelSlider):
// mouse coordinates are relative to this fixed-size item, so there is no
// drag.target / binding-fight to manage.
Item {
  id: root

  property var points: []          // [[tempC, percent], ...] ascending -- externally bound (e.g. to
                                    // editPoints/editGpuPoints); read-only from in here. Never assign
                                    // to this directly -- an imperative write would permanently sever
                                    // whatever declarative binding the caller put on it, which is
                                    // exactly the bug that used to make the CPU/GPU tabs freeze on
                                    // whichever curve was dragged first. Interaction goes through
                                    // dragPoints below instead.
  property var dragPoints: points  // live editable copy driving hit-testing, rendering and dragging
  property var ghostPoints: null   // optional second curve (e.g. the other of cpu/gpu), dimmed, not interactive
  property real liveTemp: NaN
  property string liveTempLabel: "" // e.g. "CPU" / "GPU", prefixed onto the marker text
  property real liveApplied: NaN
  property real manualPercent: NaN // NaN = auto; a number draws a pinned-speed line and dims the curve
  property color lineColor: Color.accent
  property color ghostColor: Qt.rgba(root.textColor.r, root.textColor.g, root.textColor.b, 0.3)
  property color manualColor: Color.urgent
  property color textColor: Color.foreground
  property color gridColor: Qt.rgba(root.textColor.r, root.textColor.g, root.textColor.b, 0.12)
  property color markerColor: Color.urgent
  readonly property real minTemp: 0
  readonly property real maxTemp: 100
  readonly property real minPercent: 0
  readonly property real maxPercent: 100
  readonly property real hitRadius: 14

  signal pointsEdited(var newPoints)  // committed (drag released / add / remove)

  // Re-sync the editable copy whenever the externally-bound curve changes --
  // e.g. the panel switched the CPU/GPU tab, or a save round-tripped new
  // points back in. This is the only path that should ever move data from
  // points into dragPoints once dragging has started.
  onPointsChanged: root.dragPoints = points.map(function(p) { return [p[0], p[1]] })

  implicitWidth: Style.space(420)
  implicitHeight: Style.space(220)

  readonly property real padLeft: Style.space(30)
  readonly property real padRight: Style.space(10)
  readonly property real padTop: Style.space(10)
  readonly property real padBottom: Style.space(20)
  readonly property real plotW: Math.max(1, width - padLeft - padRight)
  readonly property real plotH: Math.max(1, height - padTop - padBottom)

  function plotX(tempC) { return padLeft + (tempC - minTemp) / (maxTemp - minTemp) * plotW }
  function plotY(percent) { return padTop + plotH - (percent - minPercent) / (maxPercent - minPercent) * plotH }
  function unplotX(px) { return minTemp + (px - padLeft) / plotW * (maxTemp - minTemp) }
  function unplotY(py) { return minPercent + (padTop + plotH - py) / plotH * (maxPercent - minPercent) }
  function clampTemp(t) { return Math.max(minTemp, Math.min(maxTemp, t)) }
  function clampPercent(p) { return Math.max(minPercent, Math.min(maxPercent, p)) }

  // index of the point nearest (px,py) within hitRadius, or -1
  function hitTest(px, py) {
    var best = -1
    var bestDist = hitRadius
    for (var i = 0; i < dragPoints.length; i++) {
      var dx = plotX(dragPoints[i][0]) - px
      var dy = plotY(dragPoints[i][1]) - py
      var d = Math.sqrt(dx * dx + dy * dy)
      if (d <= bestDist) { bestDist = d; best = i }
    }
    return best
  }

  property int dragIndex: -1
  property int hoverIndex: -1  // point nearest the pointer while not dragging -- drives the tooltip on hover

  // Traces a flat-clamped curve line into ctx's current path (caller
  // strokes it) -- shared by the interactive curve and the dimmed ghost.
  function traceCurvePath(ctx, pts) {
    if (pts.length === 0) return
    ctx.moveTo(plotX(minTemp), plotY(pts[0][1]))
    ctx.lineTo(plotX(pts[0][0]), plotY(pts[0][1]))
    for (var i = 1; i < pts.length; i++)
      ctx.lineTo(plotX(pts[i][0]), plotY(pts[i][1]))
    var last = pts[pts.length - 1]
    ctx.lineTo(plotX(maxTemp), plotY(last[1]))
  }

  Canvas {
    id: canvas
    anchors.fill: parent

    Connections {
      target: root
      function onDragPointsChanged() { canvas.requestPaint() }
      function onGhostPointsChanged() { canvas.requestPaint() }
      function onLiveTempChanged() { canvas.requestPaint() }
      function onLiveAppliedChanged() { canvas.requestPaint() }
      function onManualPercentChanged() { canvas.requestPaint() }
    }
    onWidthChanged: requestPaint()
    onHeightChanged: requestPaint()
    Component.onCompleted: requestPaint()

    onPaint: {
      var ctx = getContext("2d")
      ctx.clearRect(0, 0, width, height)

      ctx.strokeStyle = root.gridColor
      ctx.lineWidth = 1
      ctx.font = "10px sans-serif"
      ctx.fillStyle = root.textColor

      var t
      for (t = root.minTemp; t <= root.maxTemp; t += 20) {
        var x = root.plotX(t)
        ctx.beginPath()
        ctx.moveTo(x, root.padTop)
        ctx.lineTo(x, root.padTop + root.plotH)
        ctx.stroke()
        ctx.fillText(Math.round(t) + "°", x - 8, root.padTop + root.plotH + 14)
      }
      var p
      for (p = root.minPercent; p <= root.maxPercent; p += 20) {
        var y = root.plotY(p)
        ctx.beginPath()
        ctx.moveTo(root.padLeft, y)
        ctx.lineTo(root.padLeft + root.plotW, y)
        ctx.stroke()
        ctx.fillText(Math.round(p) + "%", 2, y + 3)
      }

      var manualActive = !isNaN(root.manualPercent)

      // The other of cpu/gpu, if this fan has one: dimmed, for reference
      // only. Drawn first so the interactive curve renders on top of it.
      if (root.ghostPoints && root.ghostPoints.length > 0) {
        ctx.strokeStyle = root.ghostColor
        ctx.lineWidth = 1
        ctx.setLineDash([2, 3])
        ctx.beginPath()
        root.traceCurvePath(ctx, root.ghostPoints)
        ctx.stroke()
        ctx.setLineDash([])
      }

      if (root.dragPoints.length > 0) {
        // Dim the curve while a manual override is pinning the actual
        // speed, so it reads as "not currently in charge" rather than
        // implying the fan is following it.
        ctx.strokeStyle = manualActive ? Qt.rgba(root.lineColor.r, root.lineColor.g, root.lineColor.b, 0.35) : root.lineColor
        ctx.lineWidth = 2
        ctx.beginPath()
        root.traceCurvePath(ctx, root.dragPoints)
        ctx.stroke()
      }

      if (manualActive) {
        var my = root.plotY(root.clampPercent(root.manualPercent))
        ctx.strokeStyle = root.manualColor
        ctx.lineWidth = 2
        ctx.beginPath()
        ctx.moveTo(root.padLeft, my)
        ctx.lineTo(root.padLeft + root.plotW, my)
        ctx.stroke()
        ctx.fillStyle = root.manualColor
        ctx.fillText("manual " + Math.round(root.manualPercent) + "%", root.padLeft + 4, my - 4)
      }

      if (!isNaN(root.liveTemp)) {
        ctx.strokeStyle = root.markerColor
        ctx.lineWidth = 1
        ctx.setLineDash([4, 3])
        var mx = root.plotX(root.clampTemp(root.liveTemp))
        ctx.beginPath()
        ctx.moveTo(mx, root.padTop)
        ctx.lineTo(mx, root.padTop + root.plotH)
        ctx.stroke()
        ctx.setLineDash([])
        ctx.fillStyle = root.markerColor
        var prefix = root.liveTempLabel ? root.liveTempLabel + " " : ""
        ctx.fillText(prefix + Math.round(root.liveTemp) + "°C now", Math.min(mx + 4, root.width - 60), root.padTop + 10)
      }
    }
  }

  Repeater {
    model: root.dragPoints.length
    delegate: Rectangle {
      required property int index
      width: Style.space(10)
      height: Style.space(10)
      radius: width / 2
      color: root.dragIndex === index ? root.markerColor : root.lineColor
      border.color: Color.background
      border.width: 1
      x: root.plotX(root.dragPoints[index][0]) - width / 2
      y: root.plotY(root.dragPoints[index][1]) - height / 2
      z: 2
    }
  }

  // Live tooltip for whichever point is being dragged or, absent a drag,
  // hovered -- follows the dot so the temp/speed readout tracks it in
  // real time instead of only appearing on click.
  Rectangle {
    id: tooltip
    readonly property int idx: root.dragIndex >= 0 ? root.dragIndex : root.hoverIndex
    readonly property bool shown: idx >= 0 && idx < root.dragPoints.length
    readonly property var pt: shown ? root.dragPoints[idx] : [0, 0]
    readonly property real dotX: shown ? root.plotX(pt[0]) : 0
    readonly property real dotY: shown ? root.plotY(pt[1]) : 0
    readonly property bool placeBelow: dotY - height - Style.space(10) < root.padTop

    visible: shown
    z: 3
    color: Color.background
    border.color: root.lineColor
    border.width: 1
    radius: Style.space(4)
    width: label.implicitWidth + Style.space(12)
    height: label.implicitHeight + Style.space(8)
    x: Math.max(0, Math.min(root.width - width, dotX - width / 2))
    y: placeBelow ? dotY + Style.space(10) : dotY - height - Style.space(10)

    Text {
      id: label
      anchors.centerIn: parent
      textFormat: Text.PlainText
      text: tooltip.shown ? Math.round(tooltip.pt[0]) + "°C  ·  " + Math.round(tooltip.pt[1]) + "% speed" : ""
      color: root.textColor
      font.pixelSize: Style.font.caption
    }
  }

  MouseArea {
    id: plotArea
    anchors.fill: parent
    hoverEnabled: true
    cursorShape: root.dragIndex >= 0 ? Qt.ClosedHandCursor : Qt.ArrowCursor

    onPressed: function(mouse) {
      root.dragIndex = root.hitTest(mouse.x, mouse.y)
    }

    onPositionChanged: function(mouse) {
      if (root.dragIndex < 0) {
        root.hoverIndex = root.hitTest(mouse.x, mouse.y)
        return
      }
      var t = root.clampTemp(root.unplotX(mouse.x))
      var p = root.clampPercent(root.unplotY(mouse.y))
      var pts = root.dragPoints.map(function(pt) { return [pt[0], pt[1]] })
      pts[root.dragIndex] = [t, p]
      root.dragPoints = pts
    }

    onExited: root.hoverIndex = -1

    onReleased: function(mouse) {
      if (root.dragIndex < 0) return
      root.hoverIndex = root.hitTest(mouse.x, mouse.y)
      root.dragIndex = -1
      root.pointsEdited(root.dragPoints)
    }

    onDoubleClicked: function(mouse) {
      var hit = root.hitTest(mouse.x, mouse.y)
      if (hit >= 0) {
        if (root.dragPoints.length <= 2) return
        var pts = root.dragPoints.slice()
        pts.splice(hit, 1)
        root.dragPoints = pts
        root.pointsEdited(root.dragPoints)
      } else {
        var t = Math.round(root.clampTemp(root.unplotX(mouse.x)))
        var p = Math.round(root.clampPercent(root.unplotY(mouse.y)))
        var pts2 = root.dragPoints.map(function(pt) { return [pt[0], pt[1]] })
        pts2.push([t, p])
        pts2.sort(function(a, b) { return a[0] - b[0] })
        root.dragPoints = pts2
        root.pointsEdited(root.dragPoints)
      }
    }
  }
}
