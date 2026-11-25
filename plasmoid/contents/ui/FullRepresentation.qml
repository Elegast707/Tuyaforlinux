/*
 * The popup: a header with All on / All off / Refresh, and one section per lamp.
 *
 * Only Plasma/Kirigami controls and Kirigami.Theme colours are used, so the
 * popup follows the Plasma style (Oxygen, Breeze, Breeze Dark, ...).
 */
pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Layouts
import org.kde.kirigami as Kirigami
import org.kde.plasma.components as PlasmaComponents
import org.kde.plasma.extras as PlasmaExtras

PlasmaExtras.Representation {
    id: full

    // The PlasmoidItem from main.qml.
    property var backend

    Layout.minimumWidth: Kirigami.Units.gridUnit * 18
    Layout.preferredWidth: Kirigami.Units.gridUnit * 22
    Layout.minimumHeight: Kirigami.Units.gridUnit * 12
    Layout.preferredHeight: Kirigami.Units.gridUnit * 28

    collapseMarginsHint: true

    header: PlasmaExtras.PlasmoidHeading {
        RowLayout {
            anchors.fill: parent
            spacing: Kirigami.Units.smallSpacing

            PlasmaComponents.ToolButton {
                text: i18n("All on")
                icon.name: "lightbulb"
                enabled: full.backend.lampModel.count > 0
                onClicked: full.backend.setAll(true)
            }
            PlasmaComponents.ToolButton {
                text: i18n("All off")
                icon.name: "system-shutdown"
                enabled: full.backend.lampModel.count > 0
                onClicked: full.backend.setAll(false)
            }
            Item {
                Layout.fillWidth: true
            }
            PlasmaComponents.BusyIndicator {
                Layout.preferredWidth: Kirigami.Units.iconSizes.smallMedium
                Layout.preferredHeight: Kirigami.Units.iconSizes.smallMedium
                running: full.backend.listRunning
                visible: running
            }
            PlasmaComponents.ToolButton {
                text: i18n("Refresh")
                icon.name: "view-refresh"
                display: PlasmaComponents.AbstractButton.IconOnly
                onClicked: full.backend.refresh()
                PlasmaComponents.ToolTip.text: text
                PlasmaComponents.ToolTip.visible: hovered
            }
        }
    }

    contentItem: Item {
        PlasmaComponents.ScrollView {
            id: scroll
            anchors.fill: parent

            // Never scroll sideways.
            PlasmaComponents.ScrollBar.horizontal.policy: PlasmaComponents.ScrollBar.AlwaysOff
            contentWidth: availableWidth

            ColumnLayout {
                width: scroll.availableWidth
                spacing: Kirigami.Units.largeSpacing

                Repeater {
                    model: full.backend.lampModel
                    delegate: LampDelegate {
                        Layout.fillWidth: true
                        backend: full.backend
                    }
                }
            }
        }

        // Shown instead of the lamps when something is missing.
        PlasmaExtras.PlaceholderMessage {
            anchors.centerIn: parent
            width: parent.width - Kirigami.Units.gridUnit * 2
            visible: full.backend.lampModel.count === 0 && full.backend.loaded
            iconName: full.backend.errorCode === "no_command" ? "dialog-error" : "lightbulb"
            text: {
                switch (full.backend.errorCode) {
                case "no_config": return i18n("No lamps configured")
                case "no_command": return i18n("Command not found")
                case "failed": return i18n("Could not read the lamps")
                default: return i18n("No lamps found")
                }
            }
            explanation: {
                switch (full.backend.errorCode) {
                case "no_config":
                    return i18n("Run the TinyTuya wizard once inside ~/.config/tuya-lamps to create devices.json:\n\ncd ~/.config/tuya-lamps\n~/.local/share/tuya-lamps/venv/bin/python -m tinytuya wizard")
                case "no_command":
                    return i18n("Could not run “%1”. Install Tuya Lamps with ./install.sh, or set the full path (~/.local/bin/tuya-lamps) in the widget settings.", full.backend.command)
                case "failed":
                    return full.backend.errorText
                default:
                    return i18n("devices.json does not contain any lamps with a local key.")
                }
            }
        }

        // Shown while the first "list" command runs.
        PlasmaComponents.BusyIndicator {
            anchors.centerIn: parent
            visible: !full.backend.loaded
            running: visible
        }
    }

    footer: PlasmaComponents.Label {
        visible: text.length > 0
        text: full.backend.message
        wrapMode: Text.WordWrap
        padding: Kirigami.Units.smallSpacing
    }
}
