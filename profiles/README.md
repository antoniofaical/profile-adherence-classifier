# Perfis de aderência

Um perfil define o alvo, as perguntas Jev e a agregação dos critérios. O input fornece evidência; o perfil define o que conta como aderência.

Recursos:

- [Guia de autoria](HOW_TO_CREATE_PROFILES.md).
- [Schema](profile.schema.json) e [perfil mínimo](profile.template.json).
- [Briefing](profile_request.template.json) e [prompt de geração](PROFILE_GENERATION_PROMPT.md).
- Perfis da origem: `digital_twin.json`, `digital_twin_screening_v2.json`, `gsd_patient_journey_mapping.json`, `inv_mng.json`.

~~~text
profile-adherence-classifier --profile profiles/meu_perfil.json --validate-profile
profile-adherence-classifier --profile profiles/meu_perfil.json --evidence-batch coleta/ --output scores.csv
profile-adherence-classifier --profile profiles/perfil_revisado.json --evidence-batch coleta/ --output scores.csv --mode score
~~~

O recálculo usa respostas completas salvas no mesmo `--work-dir`. Pesos, papéis, limiares e agregação podem mudar mantendo perguntas iguais. Alterar a instrução comum, IDs ou perguntas exige nova classificação Jev.

JSONs de scoring e schema preservados da revisão `dbd6c4cc4fb35bb820205b500b2bdf67eb84b34b`; exemplos dos guias adaptados à interface independente. Validação automática confirma o contrato executável; o significado dos critérios depende de revisão humana.
