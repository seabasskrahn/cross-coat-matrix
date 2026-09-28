# Keeper Setup — Postgres on your own Windows PC (manual steps)

Keeper is the Matrix's working memory. It saves every job in progress (message, tasks, who
handled it, draft, pending approval, step log) so nothing is lost when the program restarts.
These steps set it up by hand. It's free and nothing goes to the cloud.

> **Note (Sep 27, 2026):** Keeper is already installed and working on DESKTOP-TTF2HMH
> (PostgreSQL 17, database `keeper`, login `matrix`). Use this guide for a reinstall or a new PC.

Do one step at a time. Each step takes a few minutes at most.

## Step 1 — Download Postgres

1. Open your browser and go to **https://www.postgresql.org/download/windows/**
2. Click **Download the installer** (it takes you to EDB, the official installer partner).
3. In the table, find **PostgreSQL 17** and click the download button in the **Windows x86-64** column.
4. Save the file (named like `postgresql-17.x-x-windows-x64.exe`).

## Step 2 — Make two passwords

You need one password for the Postgres admin and one for the Matrix. Make both the same way:

1. Press the **Windows key**, type **PowerShell**, and open it.
2. Paste this line and press Enter:
   ```powershell
   -join ((65..90)+(97..122)+(50..57) | Get-Random -Count 24 | ForEach-Object {[char]$_})
   ```
3. It prints a random password. Copy it into Notepad and label it **Admin**.
4. Run the same line again and label the second one **Matrix**.
5. Keep this Notepad file private. Don't paste these passwords into any chat.

## Step 3 — Run the installer

1. Double-click the downloaded file.
2. **Windows will ask "Do you want to allow this app to make changes to your device?" Click Yes.**
   (Windows Defender SmartScreen may also appear; click **More info**, then **Run anyway** only if
   the publisher shows EnterpriseDB.)
3. Choose these settings as you click through:

| Screen | What to choose |
| --- | --- |
| Installation Directory | Leave the default (`C:\Program Files\PostgreSQL\17`) |
| Select Components | Keep **PostgreSQL Server** and **Command Line Tools**. Untick **Stack Builder**. pgAdmin is optional (a visual viewer; fine to keep) |
| Data Directory | Leave the default |
| Password | Paste your **Admin** password in both boxes |
| Port | Leave **5432** |
| Advanced Options (Locale) | Leave **Default locale** |

4. Click **Next** until it installs, then **Finish**. If a Stack Builder window opens, close it.

Postgres now runs in the background and starts by itself whenever Windows starts.

## Step 4 — Create the Keeper database

1. Press the **Windows key**, type **SQL Shell (psql)**, and open it.
2. Press **Enter** four times to accept the defaults (Server, Database, Port, Username).
3. When it asks for the password, paste your **Admin** password and press Enter.
   (Nothing shows as you paste; that's normal.)
4. Type these two lines, pressing Enter after each. Replace `MATRIX_PASSWORD` with your **Matrix** password:
   ```sql
   CREATE ROLE matrix LOGIN PASSWORD 'MATRIX_PASSWORD';
   CREATE DATABASE keeper OWNER matrix;
   ```
   Each should answer `CREATE ROLE` and `CREATE DATABASE`.
5. Type `\q` and press Enter to close.

## Step 5 — Put the connection in `.env`

1. Open **Command Prompt** and type:
   ```
   cd %USERPROFILE%\Desktop\cross-coat-matrix
   notepad .env
   ```
2. At the bottom, add this line, replacing `MATRIX_PASSWORD` with your **Matrix** password:
   ```
   CHECKPOINT_DB_URL=postgresql://matrix:MATRIX_PASSWORD@localhost:5432/keeper
   ```
3. Save (Ctrl+S) and close Notepad.

`.env` is never uploaded to GitHub, so the password stays on your PC.

## Step 6 — Check it works

In the same Command Prompt window:
```
python -c "from matrix import graph; print(type(graph.build_graph().checkpointer).__name__)"
```
If it prints **PostgresSaver**, Keeper is live. If it prints **InMemorySaver**, the `.env` line
is missing or misspelled.

Then run the tests:
```
python -m pytest
```
All tests should pass.

## If something goes wrong

- **"password authentication failed"** — the password in `.env` doesn't match the one in Step 4.
- **"connection refused"** — Postgres isn't running. Press Windows key, type **Services**, find
  **postgresql-x64-17**, right-click, **Start**.
- Keeper only works while your PC is on.
