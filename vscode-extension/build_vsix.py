"""Build a small local VSIX without Node or npm.

Only extension source files are packaged. Use the official vsce tool for a
Marketplace release, which needs publishing metadata and review.
"""
from __future__ import annotations

import json
from pathlib import Path
from xml.etree import ElementTree as ET
from zipfile import ZIP_DEFLATED, ZipFile


HERE = Path(__file__).resolve().parent
MANIFEST = json.loads((HERE / "package.json").read_text())
OUTPUT = HERE / f"bench2agent-{MANIFEST['version']}.vsix"


def xml_bytes(element: ET.Element) -> bytes:
    return ET.tostring(element, encoding="utf-8", xml_declaration=True)


vsix_ns = "http://schemas.microsoft.com/developer/vsx-schema/2011"
ET.register_namespace("", vsix_ns)
tag = lambda name: f"{{{vsix_ns}}}{name}"
package = ET.Element(tag("PackageManifest"), {"Version": "2.0.0"})
meta = ET.SubElement(package, tag("Metadata"))
ET.SubElement(meta, tag("Identity"), {
    "Language": "en-US", "Id": MANIFEST["name"],
    "Version": MANIFEST["version"], "Publisher": MANIFEST["publisher"],
})
ET.SubElement(meta, tag("DisplayName")).text = MANIFEST["displayName"]
ET.SubElement(meta, tag("Description")).text = MANIFEST["description"]
ET.SubElement(meta, tag("Categories")).text = ",".join(MANIFEST["categories"])
properties = ET.SubElement(meta, tag("Properties"))
ET.SubElement(properties, tag("Property"), {
    "Id": "Microsoft.VisualStudio.Code.Engine", "Value": MANIFEST["engines"]["vscode"],
})
install = ET.SubElement(package, tag("Installation"))
ET.SubElement(install, tag("InstallationTarget"), {"Id": "Microsoft.VisualStudio.Code"})
ET.SubElement(package, tag("Dependencies"))
assets = ET.SubElement(package, tag("Assets"))
ET.SubElement(assets, tag("Asset"), {
    "Type": "Microsoft.VisualStudio.Code.Manifest",
    "Path": "extension/package.json", "Addressable": "true",
})
ET.SubElement(assets, tag("Asset"), {
    "Type": "Microsoft.VisualStudio.Services.Content.Details",
    "Path": "extension/README.md", "Addressable": "true",
})

types_ns = "http://schemas.openxmlformats.org/package/2006/content-types"
ET.register_namespace("", types_ns)
types = ET.Element(f"{{{types_ns}}}Types")
for extension, content_type in (
    ("vsixmanifest", "text/xml"), ("json", "application/json"),
    ("js", "application/javascript"), ("md", "text/markdown"),
):
    ET.SubElement(types, f"{{{types_ns}}}Default", {
        "Extension": extension, "ContentType": content_type,
    })

with ZipFile(OUTPUT, "w", compression=ZIP_DEFLATED) as archive:
    archive.writestr("[Content_Types].xml", xml_bytes(types))
    archive.writestr("extension.vsixmanifest", xml_bytes(package))
    for name in ("package.json", "extension.js", "runtime.js", "README.md"):
        archive.write(HERE / name, f"extension/{name}")

print(OUTPUT)
