/*
 * The controls for one lamp in the popup.
 *
 * Sliders update the model (and so the UI) immediately. The command is sent
 * when you release the slider, or 200 ms after the last change (for the
 * mouse wheel and keyboard).
 */
import QtQuick
import QtQuick.Layouts
import org.kde.kirigami as Kirigami
import org.kde.plasma.components as PlasmaComponents

ColumnLayout {
    id: lamp

    // The PlasmoidItem from main.qml.
    property var backend

    // Filled in by the Repeater from the ListModel roles.
    required property int index
    required property string lampId
    required property string name
    required property bool online
    required property bool isOn
    required property string mode
    required property int brightness
    required property int colourtemp
    required property int hue
    required property int sat
    required property bool hasColour
    required property bool hasColourtemp
    required property string error

    readonly property bool colourMode: hasColour && mode === "colour"

    // True when the user changed something that has not been sent yet.
    property bool dirty: false

    spacing: Kirigami.Units.smallSpacing

    // ---- Logic --------------------------------------------------------------

    // Update the model right away and (re)start the debounce timer.
    function change(fields) {
        fields.isOn = true // changing the light also switches the lamp on
        backend.hold(lampId)
        backend.updateLocal(index, fields)
        dirty = true
        debounce.restart()
    }

    // Send the current colour or white setting to the lamp.
    function sendLight() {
        debounce.stop()
        if (!dirty) {
            return
        }
        dirty = false
        if (colourMode) {
            backend.setLamp(lampId, ["--colour", hue, sat, "--brightness", brightness])
        } else if (hasColourtemp) {
            backend.setLamp(lampId, ["--white", colourtemp, "--brightness", brightness])
        } else {
            backend.setLamp(lampId, ["--brightness", brightness])
        }
    }

    function setMode(newMode) {
        change({ mode: newMode })
        sendLight()
    }

    // Approximate colour of the light, for the swatch.
    function previewColour() {
        if (colourMode) {
            return Qt.hsva(hue / 360, sat / 100, Math.max(brightness, 20) / 100, 1)
        }
        // White light: orange (warm) -> white -> light blue (cool).
        const t = colourtemp / 100
        const warm = Qt.rgba(1.0, 0.6, 0.24, 1)
        const cool = Qt.rgba(0.67, 0.8, 1.0, 1)
        const from = t < 0.5 ? warm : Qt.rgba(1, 1, 1, 1)
        const to = t < 0.5 ? Qt.rgba(1, 1, 1, 1) : cool
        const f = t < 0.5 ? t * 2 : (t - 0.5) * 2
        return Qt.rgba(from.r + (to.r - from.r) * f, from.g + (to.g - from.g) * f,
                       from.b + (to.b - from.b) * f, 1)
    }

    Timer {
        id: debounce
        interval: 200
        onTriggered: lamp.sendLight()
    }

    // A subtle border for the gradient bars and the swatch.
    readonly property color outline: Qt.rgba(Kirigami.Theme.textColor.r, Kirigami.Theme.textColor.g,
                                             Kirigami.Theme.textColor.b, 0.3)

    // ---- Header: dot, name, switch ----------------------------------------

    RowLayout {
        Layout.fillWidth: true
        spacing: Kirigami.Units.smallSpacing

        Rectangle {
            Layout.preferredWidth: Kirigami.Units.smallSpacing * 2
            Layout.preferredHeight: Kirigami.Units.smallSpacing * 2
            radius: width / 2
            color: lamp.online ? Kirigami.Theme.positiveTextColor
                 : lamp.error.length > 0 ? Kirigami.Theme.negativeTextColor
                 : Kirigami.Theme.disabledTextColor
        }

        PlasmaComponents.Label {
            Layout.fillWidth: true
            text: lamp.name
            elide: Text.ElideRight
            font.bold: true
        }

        Rectangle {
            id: swatch
            visible: lamp.online
            Layout.preferredWidth: Kirigami.Units.gridUnit * 1.5
            Layout.preferredHeight: Kirigami.Units.gridUnit
            radius: Kirigami.Units.cornerRadius
            color: lamp.isOn ? lamp.previewColour() : "transparent"
            border.color: lamp.outline
            border.width: 1
        }

        PlasmaComponents.Switch {
            checked: lamp.isOn
            onToggled: {
                lamp.backend.hold(lamp.lampId)
                lamp.backend.updateLocal(lamp.index, { isOn: checked })
                lamp.backend.setLamp(lamp.lampId, [checked ? "--on" : "--off"])
            }
        }
    }

    // When the lamp is unreachable, show why instead of the controls.
    PlasmaComponents.Label {
        visible: !lamp.online && lamp.error.length > 0
        Layout.fillWidth: true
        text: lamp.error
        wrapMode: Text.WordWrap
        color: Kirigami.Theme.disabledTextColor
    }

    // ---- Controls -------------------------------------------------------------

    GridLayout {
        visible: lamp.online
        Layout.fillWidth: true
        columns: 3
        columnSpacing: Kirigami.Units.smallSpacing
        rowSpacing: 0

        // Mode buttons (only for lamps that support colour).
        RowLayout {
            visible: lamp.hasColour
            Layout.columnSpan: 3
            spacing: Kirigami.Units.smallSpacing

            PlasmaComponents.Button {
                text: i18n("Colour")
                checked: lamp.colourMode
                onClicked: lamp.setMode("colour")
            }
            PlasmaComponents.Button {
                text: i18n("White")
                checked: !lamp.colourMode
                onClicked: lamp.setMode("white")
            }
        }

        // Hue
        PlasmaComponents.Label {
            visible: lamp.colourMode
            text: i18n("Hue")
        }
        ColumnLayout {
            visible: lamp.colourMode
            Layout.fillWidth: true
            Layout.columnSpan: 2
            spacing: 0
            PlasmaComponents.Slider {
                Layout.fillWidth: true
                from: 0
                to: 359
                stepSize: 1
                value: lamp.hue
                onMoved: lamp.change({ hue: Math.round(value) })
                onPressedChanged: if (!pressed) lamp.sendLight()
            }
            Rectangle {
                Layout.fillWidth: true
                Layout.preferredHeight: Kirigami.Units.smallSpacing
                radius: height / 2
                border.color: lamp.outline
                border.width: 1
                gradient: Gradient {
                    orientation: Gradient.Horizontal
                    GradientStop { position: 0 / 6; color: Qt.hsva(0 / 6, 1, 1, 1) }
                    GradientStop { position: 1 / 6; color: Qt.hsva(1 / 6, 1, 1, 1) }
                    GradientStop { position: 2 / 6; color: Qt.hsva(2 / 6, 1, 1, 1) }
                    GradientStop { position: 3 / 6; color: Qt.hsva(3 / 6, 1, 1, 1) }
                    GradientStop { position: 4 / 6; color: Qt.hsva(4 / 6, 1, 1, 1) }
                    GradientStop { position: 5 / 6; color: Qt.hsva(5 / 6, 1, 1, 1) }
                    GradientStop { position: 6 / 6; color: Qt.hsva(0, 1, 1, 1) }
                }
            }
        }

        // Saturation
        PlasmaComponents.Label {
            visible: lamp.colourMode
            text: i18n("Saturation")
        }
        PlasmaComponents.Slider {
            visible: lamp.colourMode
            Layout.fillWidth: true
            Layout.columnSpan: 2
            from: 0
            to: 100
            stepSize: 1
            value: lamp.sat
            onMoved: lamp.change({ sat: Math.round(value) })
            onPressedChanged: if (!pressed) lamp.sendLight()
        }

        // Warm <-> cool
        PlasmaComponents.Label {
            visible: !lamp.colourMode && lamp.hasColourtemp
            text: i18n("Warm ↔ cool")
        }
        ColumnLayout {
            visible: !lamp.colourMode && lamp.hasColourtemp
            Layout.fillWidth: true
            Layout.columnSpan: 2
            spacing: 0
            PlasmaComponents.Slider {
                Layout.fillWidth: true
                from: 0
                to: 100
                stepSize: 1
                value: lamp.colourtemp
                onMoved: lamp.change({ colourtemp: Math.round(value) })
                onPressedChanged: if (!pressed) lamp.sendLight()
            }
            Rectangle {
                Layout.fillWidth: true
                Layout.preferredHeight: Kirigami.Units.smallSpacing
                radius: height / 2
                border.color: lamp.outline
                border.width: 1
                gradient: Gradient {
                    orientation: Gradient.Horizontal
                    GradientStop { position: 0.0; color: Qt.rgba(1.0, 0.6, 0.24, 1) }
                    GradientStop { position: 0.5; color: Qt.rgba(1.0, 1.0, 1.0, 1) }
                    GradientStop { position: 1.0; color: Qt.rgba(0.67, 0.8, 1.0, 1) }
                }
            }
        }

        // Brightness (always)
        PlasmaComponents.Label {
            text: i18n("Brightness")
        }
        PlasmaComponents.Slider {
            Layout.fillWidth: true
            from: 1
            to: 100
            stepSize: 1
            value: lamp.brightness
            onMoved: lamp.change({ brightness: Math.round(value) })
            onPressedChanged: if (!pressed) lamp.sendLight()
        }
        PlasmaComponents.Label {
            // Reserve room for "100 %" so the slider does not jump.
            Layout.minimumWidth: Kirigami.Units.gridUnit * 2.5
            horizontalAlignment: Text.AlignRight
            text: i18n("%1 %", lamp.brightness)
        }
    }

    Kirigami.Separator {
        Layout.fillWidth: true
        Layout.topMargin: Kirigami.Units.smallSpacing
    }
}
