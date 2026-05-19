**Objet :** Rédaction des quiz — modèle CSV/Excel pour la banque de questions Flint Quiz

Bonjour à tous,

Je collecte des questions pour l'application Flint Quiz. Pour garder l'import propre, merci d'utiliser les colonnes ci-dessous (**une ligne par question**). Si une question existe dans plusieurs langues, ajoutez une ligne par langue et réutilisez le même `logical_id`.

**Structure du fichier (une ligne par question) :**

| Colonne | Obligatoire | Notes |
|---|---|---|
| `logical_id` | oui | Kebab-case en minuscules, ≤64 caractères, préfixé par l'abréviation du topic. Exemple : `az-net-vpn-001`, `az-sec-iam-012`. Le même `logical_id` est réutilisé pour les traductions. |
| `topic` | oui | ID du topic dans le catalogue, kebab-case en minuscules. Topics actuels : `azure-networking`, `azure-security`, `azure-storage`. Prévenez-moi d'abord si vous voulez proposer un nouveau topic — il doit être ajouté au catalogue. |
| `language` | oui | `en`, `fr` ou `es` (ISO 639-1, minuscules). Une ligne par langue. |
| `text` | oui | L'énoncé de la question. ≤2000 caractères. Texte brut — **pas de markdown, pas de blocs de code, pas de listes à puces** (l'énoncé est lu à voix haute par la synthèse vocale). Développez les acronymes à la première occurrence. |
| `option_a` … `option_h` | min 2, max 8 | Uniquement le texte de l'option (≤512 caractères chacune). **Ne préfixez pas** avec « A) » / « B) » — l'application ajoute le préfixe. Laissez vides les colonnes d'options non utilisées. |
| `correct_answer` | oui | La/les lettre(s) de la/les bonne(s) réponse(s), en majuscules. Généralement une seule lettre, par ex. `A`. En cas de plusieurs bonnes réponses, séparez-les par `;`, par ex. `A;C`. |
| `difficulty` | oui | L'une de `easy`, `medium`, `hard`. Visez un mélange équilibré par topic. |
| `category` | oui | Catégorie plus large que le topic, en minuscules. Exemples : `networking`, `security`, `storage`. ≤64 caractères. |
| `tags` | optionnel | Jusqu'à 16 mots-clés courts en minuscules, séparés par `;`. Exemple : `vpn;ipsec`. |
| `explanation` | oui | 1 à 2 phrases factuelles expliquant pourquoi la bonne réponse est correcte. ≤4000 caractères. Affichée à l'utilisateur **après** sa réponse. |
| `score_weight` | optionnel | Nombre décimal entre 0 et 10. Valeur par défaut : `1.0` — à modifier uniquement si vous souhaitez qu'une question pèse plus ou moins que la base. |

**Exemple de ligne complète (anglais) :**

```
logical_id, topic,             language, text,                                                                                                  option_a,            option_b,            option_c,         option_d,    correct_answer, difficulty, category,    tags,                    explanation,                                                                                                                  score_weight
az-net-agw-004, azure-networking, en,    Which Azure service provides Layer 7 load balancing with a Web Application Firewall option?, Application Gateway, Azure Load Balancer, Traffic Manager, VPN Gateway, A,              medium,     networking,  application-gateway;waf, Application Gateway is a Layer 7 load balancer with an integrated Web Application Firewall (WAF) tier; Azure Load Balancer operates at Layer 4., 1.0
```

**Quelques règles qui nous éviteront un second tour :**

1. **Une ligne par couple (logical_id, language).** Ne fusionnez pas les traductions sur une même ligne — le loader valide chaque enregistrement par langue séparément.
2. **Les traductions sont rédigées, pas traduites automatiquement.** La difficulté et l'ambiguïté doivent rester équivalentes entre `en` / `fr` / `es`. Si vous n'êtes à l'aise qu'avec une seule langue, envoyez-la et nous gérerons les deux autres.
3. **Ne mettez pas « A) » / « B) » dans le texte des options** — l'application ajoute la lettre au moment de l'affichage. Écrivez seulement le contenu de l'option.
4. **Pas de markdown ni de blocs de code** dans `text` ou `explanation`. Les questions sont lues par la synthèse vocale en mode voix.
5. **Le `logical_id` est stable à vie.** Réutilisez le même pour les lignes FR et ES ; ne le recyclez jamais pour une question différente.

CSV (UTF-8) ou `.xlsx`, les deux conviennent — je me charge de la conversion vers le format JSON attendu par le seed loader.

Merci !
