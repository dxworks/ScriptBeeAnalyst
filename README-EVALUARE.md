# ScriptBeeAssistant — Ghid de rulare

Asistent de analiză a proiectelor software: încarcă date extrase dintr-un sistem
software (cod, istoric Git, issue-uri) într-un model de tip graf și pune la
dispoziție un agent AI local care răspunde la întrebări despre proiectul analizat.

## Cerințe

- Docker + plugin-ul `docker compose`
- (pentru pasul de analiză) [OpenCode](https://opencode.ai) instalat local

## 1. Extragerea datelor

Datele de intrare sunt fișiere serializate cu informații despre proiectul
analizat (structura codului, istoric Git, issue-uri). Ele se obțin cu
următoarele tool-uri open-source:

- **Voyager** — orchestrator care rulează instrumentele de extracție asupra
  proiectului țintă: <https://github.com/dxworks/voyager>
- **GitHub Miner** — extrage date din repository-ul GitHub (commit-uri,
  pull request-uri): <https://github.com/dxworks/github-miner-2>
- **JiraMiner** — extrage issue-urile din Jira:
  <https://github.com/dxworks/jira-miner>

Rezultatul extracției este un set de fișiere serializate care se încarcă
ulterior în aplicație.

## 2. Pornirea aplicației

Întregul sistem (interfață web, API, bază de date, worker de procesare)
rulează într-un singur stack Docker:

```bash
docker compose up --build
```

Apoi se deschide interfața web la **http://localhost:8001**.
(`--build` este necesar doar la prima pornire.)

## 3. Utilizare

1. **Încărcare** — în interfața web se încarcă fișierele serializate obținute
   la pasul 1.
2. **Configurare** — se creează configurația proiectului (ce fișiere aparțin
   proiectului analizat).
3. **Finalizare** — se pornește finalizarea; sistemul construiește graful de
   date al proiectului.
4. **Analiză** — din directorul proiectului se rulează:

   ```bash
   ./analyze.sh
   ```

   Comanda deschide interfața web OpenCode cu un agent local, conectat la
   datele proiectului, cu care se poate analiza interactiv proiectul încărcat.

   **Notă:** OpenCode nu se deschide direct în directorul de lucru al
   proiectului. După deschidere, navigați în interfața OpenCode la directorul:

   ```
   analyzed_projects/projects/<numele-proiectului>
   ```
