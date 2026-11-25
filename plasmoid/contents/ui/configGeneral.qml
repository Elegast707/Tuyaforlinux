/*
 * Settings page of the widget (right-click → Configure Tuya Lamps…).
 *
 * Settings pages run inside the normal settings dialog, so they use the
 * regular QtQuick Controls (like all Plasma config pages) inside a
 * Kirigami.FormLayout.
 */
import QtQuick
import QtQuick.Controls as QQC2
import QtQuick.Layouts
import org.kde.kirigami as Kirigami
import org.kde.kcmutils as KCM

KCM.SimpleKCM {
    // Plasma connects every "cfg_<name>" property to the setting <name>
    // from contents/config/main.xml.
    property alias cfg_commandPath: commandField.text
    property alias cfg_refreshInterval: intervalSpin.value

    // Plasma also sets the default values; declaring them avoids warnings.
    property string cfg_commandPathDefault
    property int cfg_refreshIntervalDefault

    Kirigami.FormLayout {
        QQC2.TextField {
            id: commandField
            Kirigami.FormData.label: i18n("Command:")
            placeholderText: "tuya-lamps"
        }

        QQC2.Label {
            Layout.maximumWidth: Kirigami.Units.gridUnit * 22
            wrapMode: Text.WordWrap
            color: Kirigami.Theme.disabledTextColor
            text: i18n("If the widget says the command is not found, enter the full path: ~/.local/bin/tuya-lamps")
        }

        QQC2.SpinBox {
            id: intervalSpin
            Kirigami.FormData.label: i18n("Refresh every:")
            from: 5
            to: 600
            textFromValue: (value) => i18np("%1 second", "%1 seconds", value)
            valueFromText: (text) => parseInt(text)
        }
    }
}
