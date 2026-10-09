# Decisões da extração

Escopo aprovado em 09/10/2026: conteúdo + perfil → chunking → Jev → agregação → CSV com todas as empresas em ordem decrescente.

Execução: `antoniofaical/profile-adherence-classifier`. Original somente leitura: `antoniofaical/startup-theme-adherence-classifier-jev@dbd6c4cc4fb35bb820205b500b2bdf67eb84b34b`, árvore principal `src/`. Não utilizar a cópia divergente `profiles/src/`.

## Preservado

- Módulos de scoring e chunks; perfil muda somente a localização do recurso empacotado.
- Perfis, schema, IDs, versões, hashes de perfil/perguntas e critérios centrais/auxiliares.
- Payload/endpoint Jev, validação de probabilidades, timeout padrão 60 s e modelo `jev-latest`.
- Processamento integral; amostragem explícita e proveniência de páginas/chunks.
- Respostas brutas aceitas e tentativa por execução; promoção somente após conclusão.
- Recálculo offline com perguntas iguais e históricos independentes por perfil.
- Confirmação antes de chamadas pagas e autorização explícita via `--yes`.

## Alterado para a separação

- Entrada: contrato versão 1 do snowballer. Sem crawling, extração ou dependência do coletor.
- Evidência recebida somente leitura; cópias verificadas em cada lote congelam o conteúdo planejado. Origem e área de resultados são separadas.
- Reutilização exige perfil, perguntas, modelo literal, conteúdo, manifesto, empresa e digest dos chunks. Evita score de conteúdo antigo ou amostragem diferente.
- `RunStore` confere hashes de respostas/resultados e contagens. Impede reutilização de artefatos alterados; arquivos históricos sem hashes conservam a validação disponível da origem.
- CSV com uma linha por empresa, inclusive falhas, com score vazio e erro ao final. Cumpre “todas as empresas”; o export original exigia resultados completos de todas.
- Índice do classifier registra a tentativa atual sem apresentar resultado antigo como sucesso de uma tentativa nova falha.
- Importação legada gera índice próprio; não reconstrói empresas sem manifesto.
- Diretórios usam SHA-256 de nome/URL preservados. Evita colisões e nomes reservados sem mudar identidade no CSV.
- `classify` executa o fluxo completo; `score` é offline. `--dry-run` prepara plano. Guias usam a nova CLI.
- DeepL e CLI de texto avulso não migrados, conforme plano aprovado. Tradução permanece explicitamente `off`.

## Compatibilidade e limites

`tests/fixtures/original-parity.json` foi gerado executando a referência fixa. Registra revisão e hashes SHA-256 dos três arquivos de origem. Compara respostas variadas (incluindo 0 e 1), URLs repetidas, quatro métodos de scoring, três de agregação de evidência e dois perfis: 24 casos de scoring e cinco seleções de chunks. Compara resultados completos, hashes e proveniência.

CI de integração instala os pacotes separadamente, fixando snowballer em `b28ecb02b266c9fe7cefb32c1ab26ad05f1e8318`. HTTP e Jev são simulados, sem chave real nem cobrança.

Uma execução ativa por área/CSV; sem lock entre processos. Chunks permanecem em memória. `jev-latest` não garante imutabilidade do modelo. Autoria/calibração dos perfis exige validação humana. Nenhuma chamada real à API foi necessária para a implementação.
