# Demo commands cheat sheet

Run everything inside the VM. Open two Mac terminal windows and run this in each:

```bash
multipass shell senos
```

## 1. Start the app (Terminal 1)

A green speed bar at about 100% appears.

```bash
cd ~/SenOS && ./scripts/demo/start-app.sh
```

## 2. Start the background job (Terminal 2)

Terminal 1 drops to red, about 50–60%.

```bash
cd ~/SenOS && ./scripts/demo/start-build.sh
```

## 3. Show what SenOS sees (Terminal 2)

Wait about 10 seconds first.

```bash
uv run senos status
```

## 4. Let SenOS fix it (Terminal 2)

Pick one. Terminal 1 climbs back up, and the build keeps running.

Without AI (free):

```bash
uv run senos monitor start web --duration-seconds 600 --preserve build --interval 5 --apply
```

With AI (one Gemini call; needs `~/SenOS/.env`):

```bash
uv run senos agent run "Make web faster for 10 minutes but keep build running" --watch --apply
```

Optional dry run (AI only proposes and changes nothing):

```bash
uv run senos agent run "My web app is really slow because of the build. Make web faster for 10 minutes but keep build running."
```

## 5. Prove the change (optional, third terminal)

Shows 200 while SenOS runs and 100 after.

```bash
systemctl --user show web.service -p CPUWeight --value
```

## 6. Stop and clean up

1. Press **Ctrl+C** in Terminal 2. SenOS restores web to 100.
2. Press **Ctrl+C** in Terminal 1 to stop the app.
3. Stop the build and unregister both:

```bash
./scripts/demo/cleanup.sh
```

## If something goes wrong

| Problem | Fix |
|---|---|
| "Unit web.service was already loaded" | Run `./scripts/demo/cleanup.sh`, then start again |
| "Current directory does not exist" | Run `cd ~/SenOS` |
| SenOS says `no_action` | Wait another 10 seconds and try again |
| Gemini authentication error | Check the key in `~/SenOS/.env` |
| Updated `.env` on the Mac | From a Mac terminal: `scp -i ~/.ssh/senos_vm ~/Desktop/SenOS/.env ubuntu@192.168.252.2:~/SenOS/.env` |
