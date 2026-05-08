
with open(r't:\B\system_architecture_manifest.md', 'rb') as f:
    content = f.read()
    print(content[content.find(b'```mermaid'):content.find(b'```', content.find(b'```mermaid')+3)+3])
