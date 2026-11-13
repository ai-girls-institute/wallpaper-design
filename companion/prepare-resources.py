from pathlib import Path
import hashlib
import re
HERE=Path(__file__).resolve().parent
source=HERE
for path in source.glob('*.ps1'):
    path.write_text(path.read_text(encoding='utf-8-sig'),encoding='utf-8-sig',newline='\n')
def digest(name): return hashlib.sha256((source/name).read_bytes()).hexdigest()
(source/'worker_integrity.py').write_text("EXPECTED_WORKER_SHA256 = '"+digest('managed-awcc.ps1')+"'\n",encoding='utf-8')
installer=source/'persistent-install.ps1'
text=installer.read_text(encoding='utf-8-sig')
text=re.sub(r"-cne '(?:__WORKER_SHA256__|[0-9a-f]{64})' -or", "-cne '"+digest('managed-awcc.ps1')+"' -or",text)
text=re.sub(r"-cne '(?:__RUNNER_SHA256__|[0-9a-f]{64})'\)", "-cne '"+digest('persistent-runner.ps1')+"')",text)
installer.write_text(text,encoding='utf-8-sig',newline='\n')
(source/'persistent_integrity.py').write_text("INSTALLER_SHA256 = '"+digest('persistent-install.ps1')+"'\n",encoding='utf-8')
for path in source.rglob('*.py'): compile(path.read_text(encoding='utf-8-sig'),str(path),'exec')
print('Prepared resource hashes and Python syntax')
