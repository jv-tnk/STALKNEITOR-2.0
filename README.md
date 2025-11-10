# Stalkneitor 2.0

Aplicação web para centralizar progresso no USACO Guide, organizar times, notas e rankings com sincronização automática de metadados.

## Stack
- **Next.js 16 (App Router)** + TypeScript + Tailwind 4 + shadcn/ui
- **TanStack Query** para estados assíncronos no cliente
- **Supabase** (Auth + Postgres) com ORM **Drizzle**
- **Octokit** para o ETL do repositório `cpinitiative/usaco-guide`

## Estrutura principal
```
src/
  app/
    (app)/                → rotas autenticadas (dashboard, divisões, times, notas)
    api/                  → Route Handlers (catálogo, progresso, notas, times, ETL)
    docs/roadmap          → visão geral das releases
  components/
    layout/               → AppShell, navegação
    providers/            → Theme + React Query providers
    ui/                   → componentes shadcn (button, card, dialog, ...)
  lib/
    auth.ts               → helpers Supabase Auth
    constants/            → status, pesos de dificuldade
    db/                   → schema Drizzle + client
    env.ts                → validação de variáveis de ambiente
    supabase/             → clients server/browser/service-role
```

## Pré-requisitos
- Node.js ≥ 20
- npm (ou ative o corepack para usar pnpm)
- Banco Postgres/Supabase pronto e credenciais preenchidas

## Configuração rápida
1. Copie o arquivo de exemplo e preencha as variáveis necessárias:
   ```bash
   cp .env.example .env.local
   ```
2. Instale dependências e rode o servidor de desenvolvimento:
   ```bash
   npm install
   npm run dev
   ```
3. Configure o banco executando as migrações do Drizzle:
   ```bash
   npm run db:push
   ```

## Variáveis importantes
| Campo | Descrição |
| --- | --- |
| `SUPABASE_URL` / `SUPABASE_ANON_KEY` | Projeto Supabase usado pelo frontend e API |
| `SUPABASE_SERVICE_ROLE` | Chave usada apenas em rotas seguras (ex.: ETL) |
| `DATABASE_URL` | URL Postgres compatível com Drizzle/Supabase |
| `GITHUB_TOKEN` | Token readonly para aumentar o rate limit da API do GitHub |
| `ETL_SECRET` | Chave enviada no header `x-etl-secret` para `/api/sync/usaco-guide` |

## Scripts npm
| Script | Descrição |
| --- | --- |
| `npm run dev` | Next.js em modo desenvolvimento |
| `npm run build` / `start` | Build e execução em produção |
| `npm run lint` | ESLint com as regras do Next |
| `npm run db:generate` | Gera SQL a partir do schema Drizzle |
| `npm run db:push` | Sincroniza schema Drizzle com o banco |
| `npm run sync:guide` | Importa/atualiza módulos e problemas do USACO Guide no Postgres |

## Próximos passos
- Integrar Supabase Auth nas rotas protegidas (`requireUser`)
- Implementar mutations reais usando Drizzle + RLS no Supabase
- Completar o ETL com tratamento de erros/batching e armazenamento em `etl_runs`
- Conectar UI (TanStack Query) às rotas criadas
