/*
 * The panel icon. Left-click opens the popup, middle-click toggles all lamps.
 */
import QtQuick
import org.kde.kirigami as Kirigami

MouseArea {
    id: compact

    // The PlasmoidItem from main.qml.
    property var backend

    // Remember whether the popup was open when the press started; clicking
    // the icon of an open popup should close it.
    property bool wasExpanded: false

    acceptedButtons: Qt.LeftButton | Qt.MiddleButton
    hoverEnabled: true

    onPressed: wasExpanded = backend.expanded
    onClicked: (mouse) => {
        if (mouse.button === Qt.MiddleButton) {
            backend.toggleAll()
        } else {
            backend.expanded = !wasExpanded
        }
    }

    Kirigami.Icon {
        anchors.fill: parent
        source: "lightbulb"
        fallback: "preferences-desktop-color"
        active: compact.containsMouse
    }
}
