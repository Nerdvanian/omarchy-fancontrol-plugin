import QtQuick
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Ui

Panel {
  id: root
  moduleName: "nerdvanian.fancontrol"
  ipcTarget: "nerdvanian.fancontrol"
  manageIpc: false

  property var anchorItem: null
  property var hostWidget: null
  readonly property var barIdentity: hostWidget || root

  readonly property string writeScript:
    Quickshell.env("HOME") + "/.config/omarchy/plugins/" + root.moduleName + "/scripts/fancontrol-graph-write"

  // Live curve list (name/points/hysteresis_c/temp_c/applied_percent/rpm),
  // sourced from the bar widget's own poll so there is only one Process
  // reading the daemon's status/config on a timer.
  readonly property var curves: hostWidget && hostWidget.curves ? hostWidget.curves : []
  readonly property real cpuTempC: hostWidget ? hostWidget.cpuTempC : NaN
  readonly property real gpuTempC: hostWidget ? hostWidget.gpuTempC : NaN
  readonly property string hotterSource: hostWidget ? hostWidget.hotterSource : "cpu"

  property string selectedCurve: ""
  property var editPoints: []
  property real editHysteresis: 3
  property var editManualPercent: null   // null = auto (curve-controlled); number = pinned speed
  property string editLabel: ""          // custom display name; "" falls back to friendlyLabel(name)
  property string editingSource: "cpu"   // which curve the graph is currently showing/editing: "cpu" or "gpu"
  property var editGpuPoints: null       // null = this fan has no gpu profile
  property real editGpuHysteresis: 3

  function findCurve(name) {
    for (var i = 0; i < curves.length; i++) if (curves[i].name === name) return curves[i]
    return null
  }

  readonly property var selectedCurveData: findCurve(selectedCurve)
  readonly property real liveApplied: selectedCurveData && typeof selectedCurveData.applied_percent === "number" ? selectedCurveData.applied_percent : NaN
  readonly property var liveRpm: selectedCurveData ? selectedCurveData.rpm : null
  // Which sensor is actually driving the selected fan right now (only
  // meaningful for a fan that has a gpu profile; otherwise always "cpu").
  readonly property var activeSource: selectedCurveData ? selectedCurveData.active_source : null
  // The temp to show on the graph's live marker: whichever axis is
  // currently being viewed, not necessarily the one actually driving the
  // fan -- so switching to the GPU tab always shows the GPU marker even
  // if CPU is presently the hotter/driving source, and vice versa.
  readonly property real graphLiveTemp: root.editingSource === "gpu" ? root.gpuTempC : root.cpuTempC

  function friendlyLabel(name) {
    var m = /_pwm(\d+)$/.exec(name || "")
    return m ? "Fan " + m[1] : (name || "?")
  }

  // The label shown in the UI for a curve: its custom name if set, else
  // the auto-derived "Fan N".
  function displayLabel(c) {
    if (!c) return ""
    return (c.label && c.label.length > 0) ? c.label : root.friendlyLabel(c.name)
  }

  function selectCurve(name) {
    root.selectedCurve = name
    var c = root.findCurve(name)
    if (c) {
      root.editPoints = (c.points || []).map(function(p) { return [p[0], p[1]] })
      root.editHysteresis = typeof c.hysteresis_c === "number" ? c.hysteresis_c : 3
      root.editManualPercent = typeof c.manual_percent === "number" ? c.manual_percent : null
      root.editLabel = typeof c.label === "string" ? c.label : ""
      root.editGpuPoints = c.gpu_points ? c.gpu_points.map(function(p) { return [p[0], p[1]] }) : null
      root.editGpuHysteresis = typeof c.gpu_hysteresis_c === "number" ? c.gpu_hysteresis_c : 3
      root.editingSource = "cpu"  // always land on the CPU view when switching fans
      // TextField.text isn't bound to editLabel (typing would fight the
      // binding), so push the reset in explicitly -- same idiom as the
      // weather panel's location field.
      if (nameField) nameField.text = root.editLabel
    }
  }

  function renameCurve(newLabel) {
    var trimmed = (newLabel || "").toString().trim()
    root.editLabel = trimmed
    writeProc.runWith(root.selectedCurve, { label: trimmed })
  }

  property bool removeConfirmOpen: false

  function requestRemove() {
    if (root.selectedCurve) root.removeConfirmOpen = true
  }

  function cancelRemove() {
    root.removeConfirmOpen = false
  }

  function confirmRemove() {
    root.removeConfirmOpen = false
    writeProc.runWith(root.selectedCurve, { remove: true })
    // selectedCurve is left as-is (briefly stale) until the bar widget's
    // next poll drops it from root.curves; onCurvesChanged then notices
    // it's gone and switches to whatever curve is first in the list.
  }

  onCurvesChanged: {
    if (root.curves.length === 0) {
      root.selectedCurve = ""
      return
    }
    if (!root.selectedCurve || !root.findCurve(root.selectedCurve)) root.selectCurve(root.curves[0].name)
  }

  // Commits whichever curve (cpu/gpu) the graph is currently showing for
  // the selected fan. CPU fields sit at the top level of the write
  // payload; the GPU profile nests under "gpu" so it can be set or
  // cleared independently without touching the CPU curve.
  function saveCurve(newPoints) {
    if (root.editingSource === "gpu") {
      if (newPoints) root.editGpuPoints = newPoints
      writeProc.runWith(root.selectedCurve, {
        gpu: { points: root.editGpuPoints, hysteresis_c: root.editGpuHysteresis }
      })
    } else {
      if (newPoints) root.editPoints = newPoints
      writeProc.runWith(root.selectedCurve, {
        points: root.editPoints,
        hysteresis_c: root.editHysteresis
      })
    }
  }

  // Manual override is a per-fan pin, independent of which curve (cpu/gpu)
  // is currently being viewed/edited -- it bypasses both, so it gets its
  // own write rather than riding along with saveCurve().
  function setManual(enabled, percent) {
    root.editManualPercent = enabled ? Math.max(0, Math.min(100, Math.round(percent))) : null
    writeProc.runWith(root.selectedCurve, { manual_percent: root.editManualPercent })
  }

  function addGpuCurve() {
    // Default: same shape as the fan's existing CPU curve -- a starting
    // point the user can reshape, not a claim it's the right one.
    var pts = root.editPoints.map(function(p) { return [p[0], p[1]] })
    root.editGpuPoints = pts
    root.editGpuHysteresis = root.editHysteresis
    root.editingSource = "gpu"
    writeProc.runWith(root.selectedCurve, {
      gpu: { points: pts, hysteresis_c: root.editGpuHysteresis }
    })
  }

  function removeGpuCurve() {
    root.editGpuPoints = null
    root.editingSource = "cpu"
    writeProc.runWith(root.selectedCurve, { gpu: null })
  }

  function open() {
    root.controller.show()
    // Re-sync from the latest curve data every time the panel opens (not
    // just the first time) -- otherwise a change made another way (e.g.
    // manual mode toggled off elsewhere) would keep showing stale state
    // from whenever this curve was last selected.
    var name = root.selectedCurve || (root.curves.length > 0 ? root.curves[0].name : "")
    if (name) root.selectCurve(name)
  }

  function close() {
    root.controller.hide()
  }

  function toggle() {
    if (root.opened) root.close()
    else root.open()
  }

  function switchPanel(direction) {
    if (root.bar && typeof root.bar.switchPanelFrom === "function")
      return root.bar.switchPanelFrom(root.barIdentity, direction)
    return false
  }

  Process {
    id: writeProc
    property var pending: null

    function runWith(curveName, payload) {
      var args = [root.writeScript, curveName, JSON.stringify(payload)]
      if (running) { pending = args; return }
      command = args
      running = true
    }

    onExited: {
      if (pending) {
        var args = pending
        pending = null
        command = args
        running = true
      }
    }
  }

  KeyboardPanel {
    id: panel
    anchorItem: root.anchorItem
    owner: root.barIdentity
    bar: root.bar
    open: root.opened
    centerOnBar: false
    focusTarget: keyCatcher
    contentWidth: panel.fittedContentWidth(Style.space(460))
    contentHeight: panel.fittedContentHeight(mainColumn.implicitHeight)

    PanelKeyCatcher {
      id: keyCatcher
      anchors.fill: parent
      // While the rename field has focus, let it receive normal typing
      // (letters, space, arrow keys for cursor movement, Escape/Enter for
      // its own handling) instead of PanelKeyCatcher's global shortcuts
      // swallowing every keystroke. Same idiom as the weather panel's
      // location field (blocked: editingLocation).
      blocked: nameField.activeFocus || root.removeConfirmOpen
      onCloseRequested: root.close()
      onTabRequested: function(direction) { root.switchPanel(direction) }

      Column {
        id: mainColumn
        width: parent.width
        spacing: Style.space(12)

        Row {
          width: parent.width
          spacing: Style.space(8)

          Text {
            textFormat: Text.PlainText
            text: "Fan Control"
            color: root.barForeground
            font.family: root.bar ? root.bar.fontFamily : Style.font.family
            font.pixelSize: Style.font.heading
            font.bold: true
          }

          Text {
            textFormat: Text.PlainText
            anchors.verticalCenter: parent.verticalCenter
            text: {
              var s = ""
              if (!isNaN(root.cpuTempC)) s += "CPU " + Math.round(root.cpuTempC) + "°C"
              if (!isNaN(root.gpuTempC)) s += (s ? " · " : "") + "GPU " + Math.round(root.gpuTempC) + "°C"
              return s
            }
            color: Qt.darker(root.barForeground, 1.4)
            font.family: root.bar ? root.bar.fontFamily : Style.font.family
            font.pixelSize: Style.font.bodySmall
          }
        }

        Text {
          textFormat: Text.PlainText
          width: parent.width
          text: {
            if (isNaN(root.liveApplied)) return ""
            var s = Math.round(root.liveApplied) + "%"
            if (typeof root.liveRpm === "number") s += " · " + root.liveRpm + " rpm"
            if (root.editManualPercent !== null) s += " · manual"
            else if (root.activeSource === "gpu") s += " · driven by GPU"
            else if (root.editGpuPoints !== null) s += " · driven by CPU"
            return s
          }
          color: Qt.darker(root.barForeground, 1.4)
          font.family: root.bar ? root.bar.fontFamily : Style.font.family
          font.pixelSize: Style.font.bodySmall
        }

        // Fixed-width scroller for the tab row: with custom fan names, the
        // chips can easily add up to more than the card is wide. Rather
        // than trying to keep the whole popup's width in lockstep with
        // however long names happen to be, let the row scroll horizontally
        // inside a fixed frame -- this can't ever spill past the card,
        // regardless of how the tabs' natural width comes out.
        Flickable {
          width: parent.width
          height: tabs.implicitHeight
          contentWidth: tabs.implicitWidth
          contentHeight: height
          clip: true
          boundsBehavior: Flickable.StopAtBounds
          interactive: contentWidth > width

          ButtonGroup {
            id: tabs
            options: root.curves.map(function(c) { return { value: c.name, label: root.displayLabel(c) } })
            value: root.selectedCurve
            foreground: root.barForeground
            onChanged: function(v) { root.selectCurve(v) }
          }
        }

        Row {
          width: parent.width
          spacing: Style.space(8)

          Text {
            textFormat: Text.PlainText
            anchors.verticalCenter: parent.verticalCenter
            text: "Name:"
            color: Qt.darker(root.barForeground, 1.4)
            font.family: root.bar ? root.bar.fontFamily : Style.font.family
            font.pixelSize: Style.font.bodySmall
          }

          TextField {
            id: nameField
            width: Style.space(220)
            clip: true
            maximumLength: 24
            foreground: root.barForeground
            font.family: root.bar ? root.bar.fontFamily : Style.font.family
            placeholderText: root.friendlyLabel(root.selectedCurve)

            onEditingFinished: root.renameCurve(text)
            Keys.onPressed: function(event) {
              if (event.key === Qt.Key_Escape) {
                text = root.editLabel
                event.accepted = true
              }
            }
          }

          Button {
            text: "Remove"
            anchors.verticalCenter: parent.verticalCenter
            onClicked: root.requestRemove()
          }
        }

        Row {
          width: parent.width
          spacing: Style.space(12)

          ButtonGroup {
            anchors.verticalCenter: parent.verticalCenter
            options: root.editGpuPoints !== null
              ? [{ value: "cpu", label: "CPU curve" }, { value: "gpu", label: "GPU curve" }]
              : [{ value: "cpu", label: "CPU curve" }]
            value: root.editingSource
            foreground: root.barForeground
            onChanged: function(v) { root.editingSource = v }
          }

          Button {
            visible: root.editGpuPoints === null
            text: "+ GPU curve"
            anchors.verticalCenter: parent.verticalCenter
            onClicked: root.addGpuCurve()
          }

          Button {
            visible: root.editGpuPoints !== null
            text: "Remove GPU curve"
            anchors.verticalCenter: parent.verticalCenter
            onClicked: root.removeGpuCurve()
          }
        }

        CurveGraph {
          id: graph
          width: parent.width
          points: root.editingSource === "gpu" ? (root.editGpuPoints || []) : root.editPoints
          ghostPoints: root.editingSource === "gpu" ? root.editPoints : root.editGpuPoints
          liveTemp: root.graphLiveTemp
          liveTempLabel: root.editingSource === "gpu" ? "GPU" : "CPU"
          liveApplied: root.liveApplied
          manualPercent: root.editManualPercent !== null ? root.editManualPercent : NaN
          onPointsEdited: function(pts) { root.saveCurve(pts) }
        }

        Row {
          width: parent.width
          spacing: Style.space(12)

          Button {
            text: root.editManualPercent !== null ? "Manual: ON" : "Manual"
            active: root.editManualPercent !== null
            anchors.verticalCenter: parent.verticalCenter
            onClicked: {
              if (root.editManualPercent !== null) {
                root.setManual(false, 0)
              } else {
                var start = !isNaN(root.liveApplied) ? root.liveApplied : 50
                root.setManual(true, start)
              }
            }
          }

          PanelSlider {
            visible: root.editManualPercent !== null
            anchors.verticalCenter: parent.verticalCenter
            width: Style.space(200)
            minimum: 0
            maximum: 100
            integer: true
            value: root.editManualPercent !== null ? root.editManualPercent : 0
            onMoved: function(v) {
              root.editManualPercent = v
              manualDebounce.restart()
            }
            onReleased: function(v) {
              manualDebounce.stop()
              root.setManual(true, v)
            }
          }

          Text {
            visible: root.editManualPercent !== null
            textFormat: Text.PlainText
            anchors.verticalCenter: parent.verticalCenter
            text: root.editManualPercent !== null ? Math.round(root.editManualPercent) + "%" : ""
            color: root.barForeground
            font.family: root.bar ? root.bar.fontFamily : Style.font.family
            font.pixelSize: Style.font.body
          }
        }

        Timer {
          id: manualDebounce
          interval: 150
          onTriggered: if (root.editManualPercent !== null) root.setManual(true, root.editManualPercent)
        }

        Row {
          width: parent.width
          spacing: Style.space(16)

          NumberField {
            label: (root.editingSource === "gpu" ? "GPU" : "CPU") + " Hysteresis (°C)"
            value: Math.round(root.editingSource === "gpu" ? root.editGpuHysteresis : root.editHysteresis)
            from: 0
            to: 20
            onModified: function(v) {
              if (root.editingSource === "gpu") root.editGpuHysteresis = v
              else root.editHysteresis = v
              root.saveCurve(null)
            }
          }
        }

        Text {
          textFormat: Text.PlainText
          width: parent.width
          wrapMode: Text.WordWrap
          text: "Drag a point to reshape the curve · double-click empty space to add a point · double-click a point to remove it · Manual pins the fan speed until switched back off · rename a fan above and press Enter · add a GPU curve to switch this fan onto it whenever the GPU is hotter than the CPU"
          color: Qt.darker(root.barForeground, 1.6)
          font.family: root.bar ? root.bar.fontFamily : Style.font.family
          font.pixelSize: Style.font.caption
        }
      }
    }

    ConfirmDialog {
      anchors.fill: parent
      opened: root.removeConfirmOpen
      z: 10
      message: "Remove " + root.displayLabel(root.selectedCurveData) + "? It goes back to firmware/BIOS auto control -- re-run detection later to bring it back under curve control."
      confirmText: "Remove"
      background: Color.popups.background
      foreground: root.barForeground
      selectedText: Color.accent
      onCanceled: root.cancelRemove()
      onConfirmed: root.confirmRemove()
    }
  }
}
