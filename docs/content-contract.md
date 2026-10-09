# Contrato de conteúdo — versão 1

## Entrada do coletor

Lista JSON de objetos com `name` e `url`, strings não vazias. URLs HTTP(S) precisam incluir host. Nome e URL da primeira ocorrência são preservados. A deduplicação mantém a equivalência de nome ou URL da origem: esquema, `www`, consulta e barra final não criam um site novo quando host e caminho coincidem.

O lote resultante inclui todas as empresas únicas selecionadas. Entradas descartadas são contadas, não transformadas em novas empresas. A seleção por nome é opcional.

## Índice batch.json

O consumidor lê o índice dentro do diretório do lote. Caminhos são relativos a esse diretório e usam barras `/`; o consumidor deve conferir que os caminhos resolvidos permanecem dentro dele.

Campos do lote:

| Campo | Significado |
|---|---|
| `schema_version` | Inteiro 1 |
| `producer` | Nome, versão do coletor e revisão da origem |
| `status` | `running`, `completed` ou `completed_with_failures` |
| `created_at`, `completed_at` | Horários ISO 8601 UTC; conclusão é null enquanto executa |
| `input_count` | Quantidade de entradas fornecidas |
| `duplicates_skipped` | Entradas descartadas como duplicadas |
| `sites_selected` | Empresas únicas selecionadas |
| `sites_completed`, `sites_failed` | Contadores de empresas encerradas |
| `sites` | Registros na ordem das primeiras ocorrências da entrada |

Um consumidor de classificação deve esperar o lote encerrar e aceitar ambas as conclusões. Um lote interrompido pode permanecer `running`, com empresas `pending`; não equivale a uma execução completa.

Campos por empresa:

| Campo | Sucesso | Falha ou pendência |
|---|---|---|
| `name`, `url` | Identidade preservada | Identidade preservada |
| `status` | `completed` | `failed` ou `pending` |
| `operation` | `crawled`, `reused` ou `recovered` | null |
| `evidence_path`, `manifest_path`, `crawl_state_path` | Caminhos relativos | null |
| `evidence_sha256`, `manifest_sha256` | SHA-256 dos bytes dos arquivos | null |
| `pages_saved` | Número de registros de página | 0 |
| `has_text` | Presença de texto não vazio | false |
| `evidence_is_partial` | Limites ou erros de páginas | null |
| `failed_stage` | null | `crawl` ou `crawl_recovery` em falha |
| `error` | null | Objeto `{type, message}` em falha |
| `failed_attempt_path` | null | Caminho relativo para diagnóstico, quando disponível |

Uma falha nunca oferece caminhos para artefatos de tentativas anteriores. Estes podem continuar no disco para auditoria. O consumidor considera o índice corrente, e não percorre indiscriminadamente diretórios antigos.

## evidence.jsonl

UTF-8, um objeto por linha:

~~~json
{"url":"https://empresa.example/produto","content_type":"text/html","language_hint":"pt-BR","text":"Conteúdo completo extraído da página"}
~~~

Os quatro campos são strings. `language_hint` pode ser vazio. `text` pode ser vazio quando o documento não possui texto extraível. Há um registro por conteúdo salvo, após deduplicação. Nenhum score, critério de perfil ou chunk integra este arquivo.

A extração inclui HTML, texto e documentos JSON/XML; PDFs textuais exigem a dependência opcional. Não há OCR ou execução de JavaScript.

## manifest.json e crawl_state.json

O manifesto preserva os campos da origem: identidade, data, contagens, fila restante, cobertura, motivos de limite, limites, erros de requisições e erros de páginas. `crawl_config` acrescenta a configuração efetiva, para evitar reconstruir um cache sob limites diferentes.

O estado mantém `schema_version: 2`, `status: completed`, assinatura de coleta, horário e hashes de evidência e manifesto. Esse estado serve ao cache e não deve ser interpretado como garantia de cobertura total.

O índice e os JSONs são substituídos atomicamente. O conjunto de arquivos por empresa não é uma transação única: o consumidor deve esperar a conclusão do lote e verificar hashes antes de usar o conteúdo. Uma execução ativa por diretório evita gravações concorrentes de lotes distintos.

## Compatibilidade

Os registros de páginas e os nomes de arquivos seguem a origem. Manifestos antigos sem `crawl_config` podem ser recuperados após validação de identidade, contagem, texto, horário e dos limites registrados disponíveis. Não se presume que campos ausentes tenham sido registrados historicamente.

IDs e hashes de perfis são responsabilidade do classifier; o snowballer não conhece esses conceitos.

## Consumo pelo classifier

O classifier aceita lotes finalizados, confere caminhos, hashes, identidade, contagem e cobertura. Erros por empresa geram linhas sem score, sem chamada Jev para essa empresa. Registros e manifestos de entrada permanecem somente leitura; o cache de coleta não é utilizado pelo classifier.
