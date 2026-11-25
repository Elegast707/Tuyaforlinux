/*
 * Tuya Lamps - KDE Plasma 6 widget.
 *
 * This file is the "brain" of the widget. It runs the tuya-lamps command
 * line tool in the background, parses its JSON output and keeps the lamp
 * states in a ListModel. The compact (panel icon) and full (popup)
 * representations only display that model and call the functions below.
 */
import QtQuick
import org.kde.plasma.plasmoid
import org.kde.plasma.plasma5support as P5Support

PlasmoidItem {
    id: root

    // ---- State ------------------------------------------------------------

    // One entry per lamp. Roles: lampId, name, online, isOn, mode, brightness,
    // colourtemp, hue, sat, hasColour, hasColourtemp, error.
    property ListModel lampModel: ListModel {}

    // "" = all fine, "no_config" = devices.json missing,
    // "no_command" = tuya-lamps not found, "failed" = something else.
    property string errorCode: ""
    property string errorText: ""
    // Short message shown at the bottom of the popup (e.g. a failed command).
    property string message: ""
    property bool loaded: false        // true after the first "list" finished
    property bool listRunning: false   // a "list" command is running

    // Plain JavaScript bookkeeping (no bindings depend on these).
    property var queuedArgs: ({})      // lamp id -> latest waiting "set" arguments
    property var setRunning: ({})      // lamp id -> true while a "set" runs
    property var holdUntil: ({})       // lamp id -> time until refreshes may not overwrite it

    readonly property string command: Plasmoid.configuration.commandPath || "tuya-lamps"
    property int lampsOn: 0            // how many lamps are on (for tooltip and middle-click)

    Plasmoid.icon: "lightbulb"
    toolTipMainText: i18n("Tuya Lamps")
    toolTipSubText: lampModel.count === 0
        ? i18n("Middle-click to toggle all lamps")
        : i18np("%1 lamp on", "%1 lamps on", lampsOn)

    compactRepresentation: CompactRepresentation { backend: root }
    fullRepresentation: FullRepresentation { backend: root }

    // ---- Running commands -------------------------------------------------

    // The "executable" engine runs a shell command and reports its exit code,
    // stdout and stderr in onNewData. It never blocks the UI.
    P5Support.DataSource {
        id: executable
        engine: "executable"
        connectedSources: []

        property var callbacks: ({})
        property int counter: 0

        onNewData: (sourceName, data) => {
            const callback = callbacks[sourceName]
            delete callbacks[sourceName]
            disconnectSource(sourceName)
            if (callback) {
                callback(data["exit code"], data["stdout"] || "", data["stderr"] || "")
            }
        }

        function run(commandLine, callback) {
            // A source that is already connected does not run again, so we
            // make every command unique with a harmless shell comment.
            counter += 1
            const source = commandLine + " # " + counter
            callbacks[source] = callback
            connectSource(source)
        }
    }

    // Put an argument in single quotes so the shell never interprets it.
    function shellQuote(value) {
        return "'" + String(value).replace(/'/g, "'\\''") + "'"
    }

    // The command path may start with "~/", which only works unquoted.
    function quotedCommand() {
        const path = command.trim()
        if (path.startsWith("~/")) {
            return "\"$HOME\"/" + shellQuote(path.substring(2))
        }
        return shellQuote(path)
    }

    function runTool(args, callback) {
        const line = quotedCommand() + " " + args.map(shellQuote).join(" ")
        executable.run(line, callback)
    }

    // Exit code 127 is what the shell returns for "command not found".
    function isMissingCommand(exitCode, stderr) {
        return exitCode === 127 || stderr.indexOf("not found") !== -1
    }

    function parseJson(text) {
        try {
            return JSON.parse(text)
        } catch (error) {
            return null
        }
    }

    // ---- Reading the lamps ---------------------------------------------------

    function refresh() {
        if (listRunning) {
            return
        }
        listRunning = true
        runTool(["list"], (exitCode, stdout, stderr) => {
            listRunning = false
            loaded = true
            const data = parseJson(stdout)
            if (Array.isArray(data)) {
                errorCode = ""
                errorText = ""
                updateModel(data)
            } else if (data && data.code === "no_config") {
                errorCode = "no_config"
                errorText = data.error || ""
                lampModel.clear()
            } else if (isMissingCommand(exitCode, stderr)) {
                errorCode = "no_command"
                errorText = stderr.trim()
                lampModel.clear()
            } else {
                errorCode = "failed"
                errorText = (data && data.error) || stderr.trim() || i18n("Unknown error")
            }
        })
    }

    // Convert one JSON row from "tuya-lamps list" into ListModel values.
    // ListModel roles cannot be null, so we use sensible defaults.
    function toEntry(lamp) {
        return {
            lampId: String(lamp.id),
            name: String(lamp.name),
            online: lamp.online === true,
            isOn: lamp.is_on === true,
            mode: lamp.mode === "colour" ? "colour" : "white",
            brightness: lamp.brightness ?? 100,
            colourtemp: lamp.colourtemp ?? 50,
            hue: lamp.hue ?? 0,
            sat: lamp.sat ?? 100,
            hasColour: lamp.has_colour !== false,
            hasColourtemp: lamp.has_colourtemp !== false,
            error: lamp.error || ""
        }
    }

    // Is the user busy with this lamp? Then a refresh must not move its sliders.
    function isBusy(id) {
        return setRunning[id] === true || queuedArgs[id] !== undefined
            || (holdUntil[id] || 0) > Date.now()
    }

    function updateModel(rows) {
        // Same lamps in the same order: update in place so the popup
        // does not flicker and sliders being dragged are not recreated.
        let sameLamps = rows.length === lampModel.count
        for (let i = 0; sameLamps && i < rows.length; i++) {
            sameLamps = lampModel.get(i).lampId === String(rows[i].id)
        }
        if (!sameLamps) {
            lampModel.clear()
            rows.forEach(row => lampModel.append(toEntry(row)))
            return
        }
        for (let i = 0; i < rows.length; i++) {
            const entry = toEntry(rows[i])
            if (isBusy(entry.lampId)) {
                continue
            }
            // A lamp that is offline keeps its last known slider values.
            const fields = entry.online ? entry : { online: false, error: entry.error }
            for (const key in fields) {
                lampModel.setProperty(i, key, fields[key])
            }
        }
    }

    function countOn() {
        let count = 0
        for (let i = 0; i < lampModel.count; i++) {
            if (lampModel.get(i).isOn) {
                count++
            }
        }
        return count
    }

    // ---- Changing the lamps --------------------------------------------------

    // Update the model right away, so the UI reacts without waiting for the lamp.
    function updateLocal(index, fields) {
        for (const key in fields) {
            lampModel.setProperty(index, key, fields[key])
        }
    }

    function hold(id) {
        holdUntil[id] = Date.now() + 2000
    }

    // Send "tuya-lamps set <id> <args>". Only one command per lamp runs at a
    // time; if another arrives meanwhile, only the newest one is kept.
    function setLamp(id, args) {
        queuedArgs[id] = args
        if (setRunning[id] !== true) {
            startNextSet(id)
        }
    }

    function startNextSet(id) {
        const args = queuedArgs[id]
        delete queuedArgs[id]
        setRunning[id] = true
        runTool(["set", id].concat(args), (exitCode, stdout, stderr) => {
            delete setRunning[id]
            reportSetResult(parseJson(stdout), exitCode, stderr)
            if (queuedArgs[id] !== undefined) {
                startNextSet(id)
            } else {
                refresh()
            }
        })
    }

    function reportSetResult(data, exitCode, stderr) {
        const results = Array.isArray(data) ? data : (data ? [data] : [])
        const failed = results.filter(result => !result.ok)
        if (failed.length > 0) {
            message = failed.map(result => result.name + ": " + result.error).join("\n")
        } else if (exitCode !== 0) {
            message = isMissingCommand(exitCode, stderr)
                ? i18n("Command not found: %1", command)
                : (stderr.trim() || i18n("Command failed"))
        } else {
            message = ""
        }
    }

    function setAll(on) {
        for (let i = 0; i < lampModel.count; i++) {
            lampModel.setProperty(i, "isOn", on)
        }
        runTool(["set", "all", on ? "--on" : "--off"], (exitCode, stdout, stderr) => {
            reportSetResult(parseJson(stdout), exitCode, stderr)
            refresh()
        })
    }

    // Middle-click on the panel icon: if any lamp is on, switch all off.
    function toggleAll() {
        setAll(lampsOn === 0)
    }

    // Keep lampsOn up to date whenever the model changes.
    Connections {
        target: root.lampModel
        function onDataChanged() { root.lampsOn = root.countOn() }
        function onCountChanged() { root.lampsOn = root.countOn() }
    }

    // ---- Timers -------------------------------------------------------------

    // Refresh regularly, but only while the popup is open.
    Timer {
        interval: Math.max(5, Plasmoid.configuration.refreshInterval) * 1000
        running: root.expanded
        repeat: true
        triggeredOnStart: true
        onTriggered: root.refresh()
    }

    // Read the lamps once at startup, so the tooltip and middle-click work.
    Component.onCompleted: refresh()
}
