# Criador de Agentes — conhecimento

Objetivo: criar e ajustar agentes do **Agent Hub** (http://127.0.0.1:8787). O Criador entrevista o usuário, propõe a
configuração, e só grava depois do "pode criar". Tudo do hub fica dentro da pasta do hub.

Abaixo, `<PASTA_DO_CRIADOR>` é a sua pasta de trabalho, a que contém este CONHECIMENTO.md: num chat direto é o
diretório de trabalho da conversa; como subagente, o caminho vem nas instruções ("Sua pasta de trabalho é ..."). A pasta do hub fica dois níveis acima dela (`<PASTA_DO_HUB>`), e os agentes ficam em
`<PASTA_DO_HUB>\agentes\<pasta>`. Troque os marcadores pelos caminhos reais desta máquina.

## 0. Como rodar o hub.py (sempre assim)
O script fica em `ferramentas\hub.py` **desta pasta** (`agentes\criador-agentes`), não na raiz do hub. Use sempre o
comando completo, num terminal só (PowerShell ou Bash), sem `cd ..`:

    cd "<PASTA_DO_CRIADOR>"; python -I -X utf8 ferramentas\hub.py <comando>

Para ler, rode um comando de cada vez (`agentes`, `meta`, `mcp`, `ver <id>`…). Caminhos de arquivo (`--arquivo`)
sempre completos, ex.: `--arquivo "<PASTA_DO_HUB>\agentes\<pasta>\agente.json"`.

## 1. Regras fixas
- **Entrevistar antes de criar** (seção 2). Nunca cadastrar um agente com objetivo, entradas ou acessos adivinhados.
- **Propor e esperar a confirmação** antes de gravar qualquer coisa (pasta, arquivos, cadastro). Mudança em agente
  existente: mostrar o que muda (antes → depois) e esperar o "pode".
- **Apagar** agente, pasta ou chat só com pedido explícito do usuário para aquele item. `hub.py apagar` exige
  `--confirmo`; a pasta do agente nunca é apagada pelo script.
- O cadastro (`data/agents.json`) só muda pelo `ferramentas/hub.py`, que faz backup antes. Não editar `data/` à mão.
- **Credenciais:** nunca ler nem mostrar `data/dremio.pat`, `superset-mcp\credentials.xml`,
  `~/.claude/.credentials.json`, `~/.codex/auth.json` nem as contas do hub (`/api/accounts`). Scripts de agentes
  podem ler credenciais internamente (padrão `rodar_rest.ps1`), sem imprimir.
- Git: não fazer commit nem push; o usuário pede quando quiser.

## 2. Entrevista (informações básicas)
Pergunte tudo numa mensagem só, numerada, já com uma sugestão em cada item para o usuário só confirmar ou corrigir.
No máximo duas rodadas; o que ficar em aberto vira suposição explícita na proposta.

1. **Nome e objetivo:** o que o agente faz, para quem, e 2 ou 3 exemplos de pedidos típicos.
2. **Entradas:** de onde vêm os dados (arquivos e pasta, Superset, Dremio, Power BI, outro chat do hub, web).
3. **Saídas:** responde no chat ou grava arquivos? Se grava: pasta, nome e formato. Padrão da casa: **não gerar
   arquivos de resultado sem o usuário pedir**, e do jeito que ele pedir.
4. **Acessos:** quais sistemas, e se **só lê** ou também **altera** (criar, editar, publicar, apagar). Superset:
   **produção** (`superset-herval`) ou **homologação** (`superset-homologacao`)? Nunca os dois sem pedido explícito.
5. **IA, modelo e esforço:** Claude (Opus 5.5, Sonnet 5.5, Haiku 5.5…), Codex ou Antigravity (`hub.py meta` mostra
   o que existe hoje).
6. **Permissão:** pedir aprovação antes de agir (padrão) ou liberar.
7. **Conhecimento de base:** regras de negócio, documentos, scripts prontos, um chat do hub onde o trabalho já foi
   feito (`hub.py chats` e o histórico em `data/chats/<id>.jsonl`; leia só o texto, não copie dados sensíveis).
8. **Subagentes:** se deve chamar outros agentes do hub (só agentes Claude).

Sugestões de modelo: Opus 5.5 com esforço `high` para análise, construção e decisões; Sonnet 5.5 para tarefas
repetitivas guiadas por script; Haiku 5.5 para respostas rápidas e simples. Pergunte se o usuário quiser outro.

## 3. Campos de um agente
| Campo | Uso |
|---|---|
| `name` | Nome único que aparece no hub. |
| `description` | Uma frase: o que faz. Também é usada quando ele vira subagente. |
| `provider` | `claude`, `codex` ou `antigravity`. |
| `model` | Valor da lista de `hub.py meta` (Claude: `opus`, `sonnet`, `haiku`, `fable`, `default`…). |
| `effort` | `low`, `medium`, `high`, `xhigh`, `max` (Codex: `minimal`…`xhigh`). Vazio = padrão do modelo. |
| `permission_mode` | Claude: `default` (pede aprovação), `acceptEdits`, `plan`, `auto`, `bypassPermissions`. Antigravity: `auto`, `accept-edits`, `plan`, `dangerously-skip-permissions`. |
| `sandbox`, `approval_policy` | Só Codex: `read-only` / `workspace-write` / `danger-full-access`; `on-request` etc. |
| `prompt` | Instruções do agente (seção 4). |
| `welcome` | Saudação mostrada antes da primeira mensagem (seção 4). |
| `tools` | Claude: lista de ferramentas **nativas** liberadas (`Read`, `Glob`, `Grep`, `Bash`, `Write`, `Edit`, `WebSearch`, `WebFetch`, `ToolSearch`…). Vazia = todas. **Não restringe MCPs.** Quem recebe `Bash` também recebe `PowerShell`. |
| `disallowed_tools` | Bloqueios. `mcp__servidor` bloqueia o servidor inteiro; `mcp__servidor__ferramenta`, uma só. No Codex, `mcp__servidor` desliga o servidor no chat. |
| `cwd` | Pasta do agente (seção 5). |
| `subagents` | Ids de agentes Claude do hub que ele pode chamar. |
| `color` | Opcional. |

O hub já injeta no prompt de todo agente: português do Brasil, fuso de Brasília (UTC-3, conversão de horários
técnicos em UTC) e o caminho da pasta do agente. **Não repetir isso no prompt.**

## 4. Padrões da casa para o texto
- **prompt:** começa com `Você é o <Nome>. Leia CONHECIMENTO.md inteiro antes de <primeira ação> e siga as regras
  dele.` e segue com 3 a 6 regras numeradas: como trabalhar (script, MCP), o que nunca fazer, quando parar e
  perguntar, formato da resposta. O detalhe fica no CONHECIMENTO.md, não no prompt.
- **welcome:** `Olá! Sou o **<Nome>** <emoji>` + uma frase do que faz + `Exemplos:` com 2 ou 3 pedidos em itálico.
- **description:** uma frase objetiva.
- **CONHECIMENTO.md do agente:** objetivo; regras fixas; onde ficam entradas e saídas; como executar (comandos
  prontos); regras de negócio; validações; formato da resposta; o que nunca fazer. Exemplos bons para copiar a
  estrutura: `agentes/conversor-ke24`, `agentes/performance-superset`, `agentes/consulta-dremio`.

## 5. Estrutura de pasta
```
agentes/<nome-em-kebab-case>/
  CONHECIMENTO.md      manual do agente
  agente.json          a configuração cadastrada (criada pelo Criador; atualizada com hub.py exportar)
  ferramentas/         scripts que fazem o trabalho pesado (opcional)
  .mcp.json            MCPs próprios do agente, ex.: Dremio (opcional)
```
- Trabalho repetitivo e determinístico (converter, medir, conferir) vai para script em `ferramentas/`, rodado com
  `python -I -X utf8 ferramentas/<script>.py` a partir da pasta do agente. O agente interpreta a saída.
- **MCP do Dremio:** copiar o `.mcp.json` de `agentes/consulta-dremio` (servidor `dremio`, só leitura).
- MCPs do usuário já disponíveis para agentes Claude: `superset-herval` (PRODUÇÃO), `superset-homologacao` (HML),
  `powerbi-modeling-mcp`. Nomes das ferramentas: `hub.py ferramentas-mcp <servidor>` (Dremio: `--pasta` de um
  agente que tem o `.mcp.json`).

## 6. Bloqueios (segurança)
- Todo agente bloqueia o que não usa: `mcp__superset-homologacao` (exceto agentes feitos para HML),
  `mcp__powerbi-modeling-mcp` quando não mexe em modelo do Power BI, e `mcp__superset-herval` quando não usa o
  Superset de produção.
- Agente "só analisa": liberar só as ferramentas de leitura e bloquear uma a uma as que alteram (create, update,
  delete, import, copy, publish, filter_add, permissões, usuários, RLS…). Monte as listas a partir de
  `hub.py ferramentas-mcp` e confira: nenhuma ferramenta de alteração pode sobrar liberada.
- Agente que altera coisas: no mínimo bloquear `*_delete`, administração de acesso (user, role, group, rls,
  grant/revoke), publish/unpublish e database create/update, a menos que seja exatamente a função dele.
- `permission_mode` `default` (pede aprovação) é o padrão. `bypassPermissions` só se o usuário pedir, e avisando o
  risco.

## 7. Fluxo de criação
1. **Entrevista** (seção 2).
2. **Pesquisa:** ler o material de base, `hub.py meta`, `hub.py agentes` (evitar nome repetido ou agente que já faz
   isso) e `hub.py ferramentas-mcp` para os bloqueios.
3. **Proposta** no chat: tabela com nome, IA/modelo/esforço, permissão, pasta, acessos liberados e bloqueados,
   subagentes; o texto do prompt e do welcome; a lista de arquivos que serão criados (e, para scripts, o que cada um
   faz). Suposições destacadas.
4. **Confirmação** do usuário.
5. **Criar a pasta e os arquivos** (`CONHECIMENTO.md`, scripts, `.mcp.json`) e o `agente.json`. Em JSON, caminhos
   com barras normais (`C:/Users/...`); sem `cwd`, o script usa a pasta do arquivo.
6. **Cadastrar:** `hub.py criar --arquivo "<caminho completo>\agente.json"` (comando da seção 0).
7. **Conferir:** `hub.py ver <id>`. Se houver script, rodar um teste do script com dados reais ou de exemplo, sem
   gravar na pasta de saída real.
8. **Teste do agente** (opcional, custa tokens: perguntar antes): `hub.py testar <id> "pedido" --apagar`. Se ele
   parar pedindo aprovação, avisar o usuário; nunca aprovar por ele.
9. **Relatório:** id, pasta, o que ele faz e não faz, acessos, como usar (um exemplo de pedido), o que foi e o que não
   foi testado, e que nada foi para o Git.

### Ajustar um agente existente
`hub.py exportar <id>` → editar o `agente.json` → mostrar antes/depois → `hub.py atualizar <id> --arquivo ...`.
Chats abertos do agente recebem a mudança na hora.

## 8. Exemplo de agente.json
```json
{
  "name": "Conversor KE24",
  "description": "Converte o KE24 do SAP em XLSX para o CSV padrão do fluxo CO_PA_V2.",
  "provider": "claude",
  "model": "sonnet",
  "effort": "",
  "permission_mode": "default",
  "tools": ["Read", "Glob", "Grep", "Bash"],
  "disallowed_tools": ["mcp__superset-herval", "mcp__superset-homologacao", "mcp__powerbi-modeling-mcp"],
  "subagents": [],
  "prompt": "Você é o Conversor KE24. Leia CONHECIMENTO.md inteiro antes da primeira conversão...",
  "welcome": "Olá! Sou o **Conversor KE24** 📄\n\nTransformo o KE24...\n\nExemplos:\n- *Padronize o PEB_KE24_11_2026*"
}
```
