# codex-api

Coloca o **Codex CLI real da OpenAI** (`@openai/codex`) rodando dentro de um
container Docker, sempre no ar, com uma API HTTP por cima pra você chamar ele
por fora (com histórico/contexto de conversa mantido entre chamadas).

Como o Codex CLI é feito pra terminal (não é nativamente uma API), a ideia é:

- O container fica **sempre rodando** com uma API em FastAPI como processo
  principal (não desliga sozinho).
- Você faz **login uma única vez** (`codex login`), e a credencial fica
  salva num volume Docker — sobrevive a restart do container.
- A cada chamada na API, ela roda `codex exec` (ou `codex exec resume`) por
  baixo dos panos, num subprocess, e devolve a resposta via HTTP.

## Estrutura

```
codex-api/
├── docker-compose.yml
├── Dockerfile
├── requirements.txt
├── .env.example
├── workspace/               # pasta que o Codex enxerga e onde ele trabalha
└── app/
    ├── main.py              # endpoints da API (/chat, /chat/media, webhooks)
    ├── config.py            # configurações (.env)
    ├── models.py            # schemas Pydantic
    ├── history.py           # mapeamento session_id <-> thread_id (persistido)
    ├── codex_client.py      # chama o binário `codex` de verdade e parseia a saída
    ├── media.py             # STT (faster-whisper) e TTS (Piper), 100% locais
    ├── core.py              # processamento central de mensagens (texto/imagem/áudio)
    └── channels/
        ├── whatsapp.py      # adaptador Evolution API (Baileys, não-oficial)
        └── telegram.py      # adaptador Telegram Bot API (oficial)
```

## 1. Subir o container

```bash
cp .env.example .env
docker compose up --build -d
```

Isso já deixa a API rodando em `http://localhost:8000`, mas o Codex **ainda
não está logado** — sem login, toda chamada vai falhar.

## 2. Fazer login (uma única vez)

Entre no container:

```bash
docker exec -it codex-api bash
```

Você tem duas opções de login:

### Opção A — Login com conta ChatGPT (recomendado se você tem Plus/Pro/Team)

```bash
codex login --device-auth
```

Esse modo de "device auth" imprime um código e um link. Copie o link, abra
no navegador do seu computador (não precisa ser dentro do container), digite
o código, e pronto — não depende de redirecionar porta de navegador.

*(Se preferir o fluxo normal por navegador, rode `codex login` sem flags —
nesse caso a porta 1455 já está exposta no `docker-compose.yml` para o
callback do OAuth funcionar.)*

### Opção B — Login com API Key da OpenAI

```bash
printenv OPENAI_API_KEY | codex login --with-api-key
```

(rode isso depois de ter exportado sua key com `export OPENAI_API_KEY=sk-...`
antes de entrar no container, ou digite a key manualmente no lugar da variável)

### Confirmar que logou

```bash
codex login status
exit
```

Como `/root/.codex` está num **volume Docker persistente**
(`codex_home`), você só precisa logar essa vez — mesmo derrubando e subindo
o container de novo (`docker compose down` / `up`), a sessão continua válida.

## 3. Usar o painel visual

Depois de subir o container e fazer o login, abra no navegador:

```
http://localhost:8000
```

O painel (console do operador) permite, tudo pela interface:

- **Ver o status**: se o Codex está instalado e se o login está ativo (bolinha
  verde/vermelha no topo).
- **Conversar com o Codex**: digite a instrução e mande — o contexto (thread)
  é mantido automaticamente.
- **Ver e trocar entre threads**: a lista à esquerda ("ledger") mostra todas
  as conversas já iniciadas; clique numa pra retomar, ou "+ nova thread" pra
  começar do zero.
- **Apagar threads**: passe o mouse sobre uma e clique no "×".
- **Ajustar a configuração em tempo real**: clique no ícone de engrenagem (⚙)
  no topo para abrir o painel de configuração, onde dá pra mudar:
  - `exec_flags` (sandbox e política de aprovação do Codex)
  - o timeout por chamada
  - visualizar `workspace_dir` e `codex_bin` (somente leitura)

  As mudanças em `exec_flags`/timeout são salvas na hora e valem pra próxima
  chamada — **sem precisar reiniciar o container**.

## 4. Usar a API diretamente (opcional)

Se preferir chamar via `curl`/código em vez do painel:

### Checar status

```bash
curl http://localhost:8000/health
```

Retorna se o Codex está instalado e se o login está ativo.

### Mandar uma mensagem (cria sessão automaticamente)

```bash
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "Crie um arquivo hello.py que imprime Ola Mundo"}'
```

Resposta:
```json
{
  "session_id": "b3f0...",
  "thread_id": "019bd457-...",
  "reply": "Criei o arquivo hello.py com o conteúdo..."
}
```

Guarde o `session_id` — é ele que mantém o contexto.

### Continuar a mesma conversa (contexto mantido)

```bash
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"session_id": "b3f0...", "message": "agora adicione um teste pra essa função"}'
```

O Codex vai lembrar do que foi feito antes (arquivo criado, decisões
tomadas etc.) porque a chamada usa `codex exec resume <thread_id>` por trás.

### Ver / apagar uma sessão

```bash
curl http://localhost:8000/sessions/b3f0...
curl -X DELETE http://localhost:8000/sessions/b3f0...
```

### Mandar imagem e/ou áudio (`/chat/media`)

Endpoint separado, multipart, pra quando você quer mandar mídia:

```bash
# Só imagem + texto
curl -X POST http://localhost:8000/chat/media \
  -F "message=O que tem nessa imagem?" \
  -F "image=@screenshot.png"

# Só áudio (transcrito localmente e usado como prompt)
curl -X POST http://localhost:8000/chat/media \
  -F "audio=@pergunta.ogg" \
  -F "voice_reply=true"
```

Campos aceitos (todos opcionais exceto ter `message` e/ou `audio`):
- `session_id` — mesma lógica do `/chat`, mantém contexto.
- `message` — texto do prompt.
- `image` — arquivo de imagem (`png`, `jpg`, `jpeg`, `gif`, `webp`). Vai
  direto pro Codex via flag nativa `-i` (sem precisar de nenhuma API extra).
- `audio` — gravação de voz (qualquer formato que o `ffmpeg` entenda:
  `webm`, `ogg`, `mp3`, `wav`...). É transcrita localmente com
  **faster-whisper** e o texto vira o prompt (concatenado com `message`,
  se os dois vierem juntos).
- `voice_reply` — se `true`, a resposta também vem em áudio, no campo
  `audio_base64` (WAV, base64), sintetizada localmente com **Piper**
  (voz `pt_BR-faber-medium`).

Resposta:
```json
{
  "session_id": "b3f0...",
  "thread_id": "019bd457-...",
  "reply": "Essa imagem mostra...",
  "audio_base64": null
}
```

**Custo:** zero — tanto a imagem quanto o áudio usam só o que já está no
container (login do Codex + modelos locais), sem chave de API paga extra.

## 5. WhatsApp e Telegram (opcional)

Dá pra plugar o mesmo `codex-api` como bot de WhatsApp e/ou Telegram — cada
contato vira uma sessão própria (`whatsapp:5511999...`, `telegram:12345...`),
com memória de conversa independente.

### WhatsApp (via Evolution API, não-oficial)

1. Suba a [Evolution API](https://github.com/evolution-foundation/evolution-api)
   como um serviço separado (outro container Docker, no mesmo projeto do
   Railway ou onde você hospedar).
2. Crie uma instância nela e escaneie o QR Code com o número que vai atender.
3. Configure o webhook da instância apontando para
   `https://SEU-CODEX-API/webhook/whatsapp`, com o evento `MESSAGES_UPSERT`
   e `webhook_base64` ativado (pra receber imagem/áudio já em base64).
4. No `.env` do `codex-api`, preencha `EVOLUTION_API_URL`,
   `EVOLUTION_API_KEY`, `EVOLUTION_INSTANCE` (e opcionalmente
   `EVOLUTION_WEBHOOK_SECRET`).

> **Atenção:** o formato exato do payload de webhook varia entre versões da
> Evolution API. `app/channels/whatsapp.py` cobre o formato mais comum da
> v2 — se o seu vier diferente, ajuste `parse_webhook` nesse arquivo.

### Telegram (Bot API oficial)

1. Fale com [@BotFather](https://t.me/BotFather), rode `/newbot`, copie o
   token.
2. Coloque o token em `TELEGRAM_BOT_TOKEN` no `.env`.
3. Registre o webhook:
   ```bash
   curl -X POST https://api.telegram.org/bot<TOKEN>/setWebhook \
     -d url=https://SEU-CODEX-API/webhook/telegram \
     -d secret_token=<UM_SEGREDO_SEU>
   ```
4. Coloque o mesmo segredo em `TELEGRAM_WEBHOOK_SECRET` no `.env`.

Em ambos os canais: texto, imagem e áudio funcionam do mesmo jeito que no
`/chat/media` — se a pessoa mandar um áudio, a resposta também volta em
áudio automaticamente.

## Onde o Codex trabalha

Tudo que o Codex cria/edita fica na pasta `./workspace` (montada como volume),
ou seja, os arquivos aparecem no seu host, na pasta do projeto, em
`codex-api/workspace/`.

Se quiser apontar o Codex pra um repositório de verdade, é só colocar o
código lá dentro de `workspace/` antes de chamar a API.

## Ajustando o comportamento do Codex

No `.env`, a variável `EXEC_FLAGS` controla o sandbox e a política de
aprovação. Padrão:

```
EXEC_FLAGS=-s workspace-write -c approval_policy="never" --skip-git-repo-check
```

- `-s workspace-write`: o Codex pode ler/escrever arquivos dentro do
  workspace, mas não mexe no resto do sistema.
- `-c approval_policy="never"`: nunca para pra pedir aprovação humana
  (obrigatório aqui, já que rodamos sem terminal interativo).
- `--skip-git-repo-check`: permite rodar mesmo que `workspace/` não seja um
  repositório git.

Se quiser dar mais liberdade pro Codex (ex: acesso total ao sistema), dá pra
trocar por `--dangerously-bypass-approvals-and-sandbox` — mas isso é
literalmente perigoso, use só se souber bem o que está fazendo.

Depois de mudar o `.env`:

```bash
docker compose up -d --build
```

## Limitações conhecidas

- O parsing da resposta assume o formato de eventos `--json` do Codex CLI
  (`thread.started`, `item.completed` com `item.type: agent_message`). Se
  você atualizar o Codex e o formato mudar de novo, ajuste
  `app/codex_client.py` → `_parse_jsonl`.
- Cada chamada roda um `codex exec` do zero (processo novo), então tem uma
  latência de inicialização — não é um servidor "quente" mantendo estado em
  memória, o contexto é recuperado via `resume` no disco.
- Timeout padrão de 600s por chamada (`EXEC_TIMEOUT_SECONDS`), ajuste se o
  Codex costuma rodar tarefas mais longas.
- **Imagem**: só os formatos `png`, `jpg`, `jpeg`, `gif`, `webp` (o Codex CLI
  não aceita `bmp`, `tiff`, `svg` nem `heic`).
- **Áudio (STT/TTS)**: rodam localmente (`faster-whisper` + `Piper`), sem
  chave de API extra, mas com um custo: build da imagem mais pesado/lento, e
  mais CPU/RAM usados em runtime. O modelo do faster-whisper (`small`, por
  padrão) é baixado na primeira transcrição — se o container tiver pouca
  memória, troque para `"base"` ou `"tiny"` em `app/media.py`.
- **WhatsApp via Evolution API**: é uma integração não-oficial (baseada em
  engenharia reversa do protocolo do WhatsApp via Baileys). Funciona bem na
  prática, mas carrega o risco inerente de instabilidade entre versões e de
  banimento de número pelo WhatsApp em uso muito agressivo/automatizado —
  isso é uma característica da abordagem, não um bug do código aqui.
- O payload de webhook da Evolution API varia entre versões; valide o
  formato real antes de confiar 100% no parsing em
  `app/channels/whatsapp.py`.
