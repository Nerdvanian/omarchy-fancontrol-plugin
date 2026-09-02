import QtQuick
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Ui

BarWidget {
  id: root
  moduleName: "nerdvanian.fancontrol"

  readonly property string dataScript:
    Quickshell.env("HOME") + "/.config/omarchy/plugins/" + root.moduleName + "/scripts/fancontrol-graph-data"

  readonly property string fanIcon: ""

  property var curves: []
  property real updatedAt: 0
  property real cpuTempC: NaN
  property real gpuTempC: NaN
  property string hotterSource: "cpu"

  function injectPanel() {
    var target = panelLoader.item
    if (!target) return
    if ("bar" in target) target.bar = root.bar
    if ("settings" in target) target.settings = root.settings
    if ("anchorItem" in target) target.anchorItem = button
    if ("hostWidget" in target) target.hostWidget = root
  }

  readonly property bool opened: panelLoader.item ? panelLoader.item.opened === true : false

  function open() {
    if (panelLoader.item && panelLoader.item.open) panelLoader.item.open()
  }

  function close() {
    if (panelLoader.item && panelLoader.item.close) panelLoader.item.close()
  }

  function togglePanel() {
    if (panelLoader.item && panelLoader.item.toggle) panelLoader.item.toggle()
  }

  readonly property bool popoutSwitchClosing: panelLoader.item ? panelLoader.item.popoutSwitchClosing === true : false

  function closeForPopoutSwitch() {
    if (panelLoader.item) panelLoader.item.closeForPopoutSwitch()
  }

  onBarChanged: injectPanel()
  onSettingsChanged: injectPanel()

  readonly property real maxPercent: {
    var m = 0
    for (var i = 0; i < curves.length; i++) {
      var p = curves[i].applied_percent
      if (typeof p === "number" && p > m) m = p
    }
    return m
  }

  readonly property bool stale: updatedAt > 0 && (Date.now() / 1000 - updatedAt) > 10

  // CPU/GPU temps at a glance -- fan speed % and per-fan RPM move to the
  // hover tooltip instead, since curve tuning is the reason to open the
  // panel, not a speed readout.
  readonly property string pillText: {
    if (isNaN(cpuTempC) && isNaN(gpuTempC)) return fanIcon + " --"
    var parts = []
    if (!isNaN(cpuTempC)) parts.push("C " + Math.round(cpuTempC) + "°")
    if (!isNaN(gpuTempC)) parts.push("G " + Math.round(gpuTempC) + "°")
    return fanIcon + " " + parts.join(" ")
  }

  function friendlyLabel(name) {
    var m = /_pwm(\d+)$/.exec(name || "")
    return m ? "Fan " + m[1] : (name || "?")
  }

  readonly property string tooltipText: {
    var lines = []
    var head = ""
    if (!isNaN(cpuTempC)) head += "CPU " + Math.round(cpuTempC) + "°C"
    if (!isNaN(gpuTempC)) head += (head ? " · " : "") + "GPU " + Math.round(gpuTempC) + "°C"
    if (head) lines.push(head)
    if (curves.length === 0) {
      lines.push("No fan curves configured")
      return lines.join("\n")
    }
    for (var i = 0; i < curves.length; i++) {
      var c = curves[i]
      var line = ((c.label && c.label.length > 0) ? c.label : root.friendlyLabel(c.name)) + ": "
      line += (typeof c.temp_c === "number" ? Math.round(c.temp_c) + "°C" : "?°C")
      line += " · " + (typeof c.applied_percent === "number" ? Math.round(c.applied_percent) + "%" : "?%")
      if (typeof c.rpm === "number") line += " · " + c.rpm + "rpm"
      lines.push(line)
    }
    if (stale) lines.unshift("(stale)")
    return lines.join("\n")
  }

  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight

  Loader {
    id: panelLoader
    active: true
    source: Qt.resolvedUrl("Panel.qml")
    visible: false
    onLoaded: {
      root.injectPanel()
      Qt.callLater(root.injectPanel)
    }
  }

  WidgetButton {
    id: button
    bar: root.bar
    text: root.pillText
    tooltipText: root.tooltipText

    onPressed: function(b) {
      if (!root.bar) return
      if (b === Qt.RightButton) {
        root.bar.run("omarchy-launch-floating-terminal-with-presentation ~/.local/share/omarchy-fancontrol/fancontrol-watch")
      } else {
        root.togglePanel()
      }
    }
  }

  Process {
    id: dataProc
    command: [root.dataScript]
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        try {
          var data = JSON.parse(text)
          root.curves = data.curves || []
          root.updatedAt = data.updated || 0
          root.cpuTempC = typeof data.cpu_temp_c === "number" ? data.cpu_temp_c : NaN
          root.gpuTempC = typeof data.gpu_temp_c === "number" ? data.gpu_temp_c : NaN
          root.hotterSource = data.hotter_source || "cpu"
        } catch (e) {
          // leave last-known state on parse failure (e.g. daemon mid-write)
        }
      }
    }
  }

  Timer {
    interval: 3000
    running: true
    repeat: true
    triggeredOnStart: true
    onTriggered: if (!dataProc.running) dataProc.running = true
  }
}
