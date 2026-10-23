# Windows service installation

Docker is the easiest portable installation. For a dedicated Windows show computer, the repository also includes a native Windows service installer.

Requirements:

- Python 3.10
- `uv`
- FFmpeg and FFprobe on `PATH`
- Node.js for rebuilding the controller
- a writable work directory with space for previews and prepared media

From an Administrator PowerShell window:

```powershell
git clone https://github.com/d8ahazard/projector.git
cd projector
copy docker\.env.example docker\.env
.\scripts\Install-ServerService.ps1
```

The installer registers `ProjectorServer` as Automatic (Delayed Start), configures restart recovery, and creates private-LAN firewall rules for TCP 8000 and mDNS. It uses LocalService and keeps logs, temporary files, media caches, and release files outside the source checkout.

Review the script before installation and set your public LAN host, work directory, and source root through environment variables. The web controller is rebuilt with `npm run build` under `web/`.

Check health:

```powershell
Get-Service ProjectorServer
Invoke-RestMethod http://127.0.0.1:8000/api/info
```

Do not run the manual launcher, Docker server, and Windows service on port 8000 at the same time.
