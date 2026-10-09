# Profile Adherence Classifier

Recebe conteúdo de páginas e um perfil, cria chunks, consulta a Jev API e agrega as respostas em um score por empresa. Exporta todas as empresas em CSV, com scores válidos em ordem decrescente e falhas ao final, com score vazio.

## Instalação e uso

Python 3.11 ou superior, em ambiente virtual:

~~~text
python -m pip install .
profile-adherence-classifier --evidence-batch coleta/batch.json --profile profiles/tema.json --output scores.csv
~~~

Também aceita o diretório `coleta/` e funciona com `python -m profile_adherence_classifier`. Sem `--profile`, usa o perfil `digital_twin` incluído no pacote. Funciona sozinho: suas dependências são `requests` e `jsonschema`, sem instalação do snowballer.

Defina a chave Jev na variável `TYPESAFE_PSN_DIG_TWIN_CLASS`, preservada da origem. `--api-key-env` permite escolher outra variável. O comando verifica evidências, informa o número de chamadas e solicita confirmação antes das chamadas pagas. Para execução não interativa, passe `--yes` explicitamente.

~~~text
profile-adherence-classifier --evidence-batch coleta/ --profile profiles/tema.json --output scores.csv --workers 4 --yes
profile-adherence-classifier --evidence-batch coleta/ --output scores.csv --dry-run
profile-adherence-classifier --profile profiles/tema.json --validate-profile
~~~

`--workers` paraleliza empresas, com padrão 1. Os chunks de cada empresa são enviados sequencialmente. Uma falha não interrompe as demais empresas.

## Conteúdo, score e CSV

A entrada segue o [contrato versão 1](docs/content-contract.md) do snowballer: índice finalizado com todas as empresas e `evidence.jsonl` de cada coleta. O conteúdo é texto extraído de páginas, com URLs e metadados. Este pacote não faz crawling nem extração de HTML.

Todo o texto utilizável é processado por padrão, em chunks de até 20.000 caracteres (`--chunk-chars`). Como na origem, espaços externos de cada página são retirados. Não há truncamento silencioso. `--percentage` ou `--max-chunks` ativam amostragem explícita, registrada como parcial quando reduz a cobertura.

O score segue o perfil: agregação de evidência por critério e, depois, dos critérios centrais. Critérios auxiliares geram colunas e flags próprias. IDs, hashes de perguntas/perfis e fórmulas foram preservados. Somar ou concatenar scores de chunks não substitui essas fórmulas.

O CSV mantém `site_name`, `site_url`, identidade do perfil, `fit_score`, critérios `core__...` e `aux__...`, cobertura e principais força/lacuna. Acrescenta `status`, `failed_stage` e `error`. Empates são ordenados por nome. Falhas de coleta, ausência de texto ou classificação incompleta não recebem score consolidado; aparecem ao final.

## Resultados e reutilização

A área própria padrão é `classification/`, ao lado do CSV; escolha outra com `--work-dir`. CSV e área de trabalho devem ficar fora da entrada; o CSV também deve ficar fora da área de trabalho. A entrada permanece somente leitura; cópias verificadas congelam os bytes antes das chamadas pagas.

~~~text
classification/
  batches/<batch-id>/
    batch.json
    content-index.json
    profile.json
    inputs/<company-key>/evidence.jsonl
    inputs/<company-key>/manifest.json
    results/<company-key>.json
  companies/<company-key>/profiles/<profile-id>/<version>/
    current.json
    runs/<run-id>/
      profile.json
      run.json
      responses.jsonl
      result.json
      derived/<profile-hash>.json
~~~

Respostas aceitas, probabilidades e respostas brutas são salvas antes da agregação. Uma tentativa incompleta mantém log parcial e não substitui a execução completa anterior. O CSV corrente mostra a falha dessa tentativa. O índice é atualizado conforme as empresas terminam.

Por padrão, uma nova execução classifica novamente. `--only-missing` reutiliza somente resultados completos com empresa, perfil, perguntas, modelo, conteúdo, manifesto e seleção de chunks compatíveis. O CSV inclui as empresas reutilizadas.

~~~text
profile-adherence-classifier --evidence-batch coleta/ --profile profiles/tema.json --output scores.csv --only-missing --yes
profile-adherence-classifier --evidence-batch coleta/ --profile profiles/tema-revisado.json --output scores.csv --mode score
~~~

`--mode score` recalcula localmente a partir das respostas completas salvas, sem chave nem chamada Jev. Aceita mudanças de pesos, papéis, limiares e fórmulas com perguntas iguais; exige o mesmo conteúdo, cobertura, modelo e chunks. Use o mesmo `--work-dir` e parâmetros da classificação inicial. `--model` padrão é `jev-latest`; esse nome literal não identifica uma versão imutável do provedor.

## Coletas antigas e controles

~~~text
profile-adherence-classifier --import-legacy evidence-antiga/ --profile profiles/tema.json --output scores.csv
~~~

O importador percorre `*/manifest.json`, verifica identidade, conteúdo e contagem e cria o índice na área do classifier. Não altera arquivos antigos. Empresas sem manifesto não podem ser reconstruídas somente desses arquivos. Hashes calculados na importação descrevem os bytes atuais; não comprovam integridade histórica.

`--request-timeout` padrão 60 segundos; `--no-progress` oculta progresso; `-v` mostra erros; `--traceback` inclui detalhes. `--dry-run` salva plano e cópias verificadas, sem chamadas Jev e sem exportar CSV.

Códigos de saída: **0** para lote sem falhas; **1** para falhas por empresa ou cancelamento; **2** para entrada/opções/configuração inválidas ou erro geral. O CSV é escrito mesmo com falhas por empresa ou cancelamento. Lote globalmente inválido é recusado antes da exportação. Use uma execução ativa por área de trabalho e CSV.

Planejamento mantém chunks em memória, como na origem. A integração usa HTTP local e Jev simulada; não comprova disponibilidade, custo ou qualidade da API real. DeepL e texto avulso ficam fora desta primeira entrega.

## Verificação e origem

~~~text
python -m pip install -e ".[dev]"
python -m ruff check src tests tools
python -m ruff format --check src tests tools
python -m pytest -q
python -m build --wheel
~~~

O CI verifica Linux/Python 3.11 e 3.12, Windows/3.12, instalação isolada e integração dos dois pacotes. Testes unitários bloqueiam a rede. Fixtures numéricas foram geradas a partir da revisão original e cobrem todos os métodos de agregação.

Origem somente leitura: [startup-theme-adherence-classifier-jev@dbd6c4c](https://github.com/antoniofaical/startup-theme-adherence-classifier-jev/tree/dbd6c4cc4fb35bb820205b500b2bdf67eb84b34b), árvore principal `src/`. Consulte [decisões de extração](docs/extraction-decisions.md) e [autoria de perfis](profiles/README.md).
