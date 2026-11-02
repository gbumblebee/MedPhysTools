# Matrice exigences ↔ tests : phase 0

**État : le critère de passage est atteint sous Fedora. La compatibilité Windows reste à vérifier.**

Un test vérifie que chaque test cité ici existe (`tests/test_scenario.py`). Les noms sont donc garantis, mais pas la pertinence du lien, qui reste à relire.

## Critère de passage

| Exigence | Test | État |
|---|---|---|
| Une page se relit en une ligne de Python, dans un processus neuf | `tests/test_api.py::test_le_critere_de_la_phase_0_dans_un_processus_neuf` | Vérifié |
| Même chose sur une archive copiée ailleurs | `tests/test_scenario.py::test_critere_de_passage_sur_une_archive_copiee_ailleurs` | Vérifié |
| L'exemple de la section 7 s'exécute tel quel | `tests/test_api.py::test_l_exemple_de_la_section_7_s_execute_tel_quel` | Vérifié |

## Cœur sans interface

| Exigence | Test | État |
|---|---|---|
| Le cœur ne charge pas Qt | `tests/test_socle.py::test_importer_pymphub_ne_charge_pas_qt` | Vérifié |
| Les sources du cœur n'importent pas Qt | `tests/test_socle.py::test_les_sources_du_coeur_n_importent_pas_qt` | Vérifié |

## Unités

| Exigence | Test | État |
|---|---|---|
| Conversion MBq ↔ mCi exacte | `tests/test_unites.py::test_conversion_mbq_mci` | Vérifié |
| Conversion sans sens physique refusée | `tests/test_unites.py::test_une_conversion_sans_sens_physique_est_refusee` | Vérifié |
| Catalogue compris par pint et MetroloPy | `tests/test_unites.py::test_le_catalogue_est_compris_par_pint_et_metrolopy` | Vérifié |
| Mêmes facteurs de conversion | `tests/test_unites.py::test_pint_et_metrolopy_donnent_la_meme_conversion` | Vérifié sur 13 paires |
| Suggestion en cas de syntaxe fausse | `tests/test_unites.py::test_une_erreur_de_syntaxe_recoit_une_suggestion_valide` | Vérifié |
| Unité vide refusée | `tests/test_unites.py::test_une_unite_vide_est_refusee` | Vérifié |

## Incertitudes

| Exigence | Test | État |
|---|---|---|
| Budget calculé à la main retrouvé | `tests/test_incertitudes.py::test_budget_calcule_a_la_main` | Vérifié |
| U en absolu et en relatif | `tests/test_incertitudes.py::test_incertitude_elargie_absolue_et_relative` | Vérifié |
| k change l'affichage, pas les données | `tests/test_incertitudes.py::test_k_change_l_affichage_mais_pas_les_donnees` | Vérifié |
| Pas de décalage de l'incertitude en °C ↔ K | `tests/test_incertitudes.py::test_une_conversion_avec_decalage_ne_decale_pas_l_incertitude` | Vérifié |
| Répétitions : moyenne et type A | `tests/test_incertitudes.py::test_repetitions_donnent_moyenne_et_type_a` | Vérifié |
| Propagation jusqu'à un résultat final | `tests/test_scenario.py::test_propagation_jusqu_a_la_periode_radioactive` | Vérifié au premier ordre |
| Limite : budgets distincts indépendants | `tests/test_incertitudes.py::test_deux_budgets_distincts_sont_independants` | Limite documentée |

## Modèle de page

| Exigence | Test | État |
|---|---|---|
| Aller-retour JSON identique | `tests/test_page.py::test_aller_retour_json_identique` | Vérifié |
| Champs invalides refusés (fuseau, machine…) | `tests/test_page.py::test_champ_de_page_invalide_refuse` | Vérifié |
| Identifiant exact | `tests/test_page.py::test_un_identifiant_reste_exact` | Vérifié |
| Page sans gabarit valide | `tests/test_page.py::test_une_page_sans_gabarit_ni_contenu_est_valide` | Vérifié |
| Unité et incertitude obligatoires | `tests/test_page.py::test_unite_et_incertitude_sont_obligatoires` | Vérifié |
| Valeur exacte déclarée | `tests/test_page.py::test_une_valeur_exacte_declare_une_incertitude_nulle` | Vérifié |
| Nom de grandeur unique dans la page | `tests/test_page.py::test_un_nom_repete_dans_la_page_est_refuse` | Vérifié |
| Nom de colonne unique dans le tableau | `tests/test_page.py::test_une_colonne_repetee_dans_un_tableau_est_refusee` | Vérifié |
| Déplacer une grandeur sans changer sa relecture | `tests/test_page.py::test_deplacer_une_grandeur_d_onglet_ne_change_pas_sa_relecture` | Vérifié |
| Ajouter un type d'onglet par une déclaration | `tests/test_page.py::test_ajouter_un_type_d_onglet_ne_demande_qu_une_declaration` | Vérifié dans le cœur ; affichage en phase 1 |
| Type d'onglet inconnu conservé | `tests/test_page.py::test_un_type_d_onglet_inconnu_est_conserve_intact` | Vérifié |
| Commentaire propre à chaque colonne d'un tableau | `tests/test_interface_tableau.py::test_chaque_colonne_garde_son_commentaire` | Vérifié |
| Annoter une photo laisse son fichier identique | `tests/test_interface_photos.py::test_annoter_laisse_le_fichier_d_origine_identique` | Vérifié |
| Annotations retrouvées après réouverture | `tests/test_interface_photos.py::test_les_annotations_se_retrouvent_apres_reouverture` | Vérifié |
| Annotation au même endroit quelle que soit la taille d'affichage | `tests/test_interface_photos.py::test_une_annotation_reste_au_meme_endroit_quelle_que_soit_la_taille` | Vérifié |
| Fichier altéré signalé à l'affichage | `tests/test_interface_photos.py::test_un_fichier_altere_est_signale` | Vérifié |
| Fichier manquant signalé à l'affichage | `tests/test_interface_photos.py::test_un_fichier_manquant_est_signale_sans_plantage` | Vérifié |
| Aucun DICOM avant la pseudonymisation | `tests/test_interface_photos.py::test_un_dicom_n_entre_pas_dans_l_archive` | Vérifié dans l'interface ; l'API Python ne le refuse pas |
| Commentaire d'une pièce jointe | `tests/test_interface_photos.py::test_commenter_une_piece_jointe` | Vérifié |
| Import personnalisé de scalaires et d'informations | `tests/test_interface_tableau.py::test_un_import_complet_remplit_le_tableau_et_les_scalaires` | Vérifié |
| Une valeur importée sans incertitude ne s'enregistre pas | `tests/test_interface_tableau.py::test_un_scalaire_importe_sans_incertitude_reste_a_completer` | Vérifié |
| Un import n'écrase aucune mesure | `tests/test_interface_tableau.py::test_un_nom_deja_pris_n_ecrase_rien` | Vérifié |
| Fichier importé conservé en pièce jointe | `tests/test_interface_photos.py::test_le_fichier_importe_devient_une_piece_jointe` | Vérifié |
| Suppression d'une page tracée, numéro jamais réutilisé | `tests/test_archive.py::test_le_numero_d_une_page_supprimee_ne_resert_pas` | Vérifié |
| Annotations à part de la photo | `tests/test_page.py::test_les_annotations_sont_enregistrees_a_part_de_la_photo` | Vérifié |
| Repère d'erreur commune conservé | `tests/test_page.py::test_l_erreur_commune_est_conservee_dans_une_page` | Vérifié ; pas utilisé en calcul |

## Gabarits

| Exigence | Test | État |
|---|---|---|
| Aucune donnée mesurée dans le gabarit | `tests/test_gabarit.py::test_le_gabarit_ne_contient_aucune_donnee` | Vérifié |
| Aller-retour TOML identique | `tests/test_gabarit.py::test_aller_retour_toml_identique` | Vérifié |
| Page créée depuis un gabarit | `tests/test_gabarit.py::test_une_page_creee_depuis_un_gabarit_garde_sa_version_et_liste_ce_qui_reste` | Vérifié |
| Page remplie conforme | `tests/test_gabarit.py::test_une_page_remplie_selon_le_gabarit_devient_conforme` | Vérifié |
| Variable absente signalée | `tests/test_gabarit.py::test_une_variable_absente_est_signalee` | Vérifié |
| Unité incohérente signalée | `tests/test_gabarit.py::test_une_unite_incoherente_est_signalee` | Vérifié |

## Archive sur disque

| Exigence | Test | État |
|---|---|---|
| Archive déplacée toujours lisible | `tests/test_archive.py::test_une_archive_deplacee_s_ouvre_toujours` | Vérifié |
| Format trop récent refusé | `tests/test_archive.py::test_format_trop_recent_refuse_meme_avec_des_champs_inconnus` | Vérifié |
| Noms invalides sous Windows refusés | `tests/test_archive.py::test_nom_invalide_sous_windows_refuse` | Vérifié par règles, sous Fedora |
| Écriture atomique | `tests/test_archive.py::test_ecriture_interrompue_laisse_l_ancien_fichier_intact` | Vérifié par simulation |
| Préfixe des chemins longs | `tests/test_archive.py::test_prefixe_des_chemins_longs_windows` | Logique vérifiée ; **à confirmer sous Windows** |
| Archive au chemin très long | `tests/test_archive.py::test_une_archive_au_chemin_tres_long_fonctionne` | Vérifié sous Fedora ; **à confirmer sous Windows** |
| Rétrocompatibilité du format | `tests/test_scenario.py::test_chaque_version_du_format_a_son_archive_figee` | Vérifié |

## Traçabilité

| Exigence | Test | État |
|---|---|---|
| Commit à chaque enregistrement d'un brouillon | `tests/test_historique.py::test_chaque_enregistrement_d_un_brouillon_cree_un_commit_sans_motif` | Vérifié |
| Motif obligatoire après validation | `tests/test_historique.py::test_une_correction_sans_motif_est_refusee_sur_une_page_validee` | Vérifié |
| Ancienne valeur retrouvée | `tests/test_historique.py::test_l_ancienne_valeur_est_retrouvee` | Vérifié |
| Corrections de toute l'archive | `tests/test_historique.py::test_corrections_de_toute_l_archive` | Vérifié |
| Compatible avec Git standard | `tests/test_historique.py::test_git_fsck_ne_signale_rien` | Vérifié |
| Modification externe non écrasée | `tests/test_historique.py::test_une_modification_a_la_main_n_est_pas_ecrasee` | Vérifié |
| Fichiers lourds hors de Git | `tests/test_historique.py::test_les_fichiers_lourds_restent_hors_de_git` | Vérifié |

## Fichiers bruts et tableaux

| Exigence | Test | État |
|---|---|---|
| Déduplication | `tests/test_fichiers.py::test_un_fichier_importe_deux_fois_n_est_stocke_qu_une_fois` | Vérifié |
| Altération signalée sans écriture | `tests/test_fichiers.py::test_une_copie_alteree_est_signalee_sans_rien_ecrire` | Vérifié |
| Remplacement d'un fichier lourd tracé | `tests/test_fichiers.py::test_le_remplacement_d_un_fichier_lourd_est_trace` | Vérifié |
| Identifiants non convertis dans un CSV | `tests/test_fichiers.py::test_les_identifiants_ne_sont_pas_convertis` | Vérifié |
| Motif pour une cellule validée | `tests/test_fichiers.py::test_une_cellule_d_une_page_validee_exige_un_motif` | Vérifié |

## Recherche

| Exigence | Test | État |
|---|---|---|
| Critères combinés | `tests/test_index.py::test_criteres_combines` | Vérifié |
| Index reconstruit à l'identique | `tests/test_index.py::test_supprime_il_se_reconstruit_a_l_identique` | Vérifié |
| Index périmé détecté | `tests/test_index.py::test_un_index_perime_est_detecte` | Vérifié |
| Recherche rapide sur 5 000 pages | `tests/test_index.py::test_construction_et_recherche_sur_5000_pages` | Vérifié sous Fedora, sur disque local |

## Ce qui n'est pas vérifié

| Sujet | État | Quand |
|---|---|---|
| Tout le logiciel sous Windows : chemins longs, dulwich, SQLite, magasin en lecture seule | Jamais exécuté | À l'arrivée du poste, vers mi-décembre 2026 |
| Python embarqué sur un PC d'hôpital : antivirus, `.bat`, réseau | Jamais exécuté | Dès l'accès à un tel poste |
| Performances sur un partage réseau | Jamais mesurées | Avec le poste Windows |
| Données patient dans les noms de fichiers et les DICOM | **Non traité** : ne pas importer de données patient | Avant les premiers DICOM réels, en phase 1 |
| Calculs avec les erreurs communes entre mesures | Repère stocké, calcul à faire | À décider ; nécessaire pour des rapports exacts |
| Compression des DICOM et empreinte des pixels | Non implémentées (`pydicom` absent) | Phase 1 |
| Vérification globale d'intégrité | Non implémentée ; contrôle à l'accès seulement | Phase 3 |
| Données réelles | Scénario à valeurs fictives | Premier CQ réel, en phase 1 |
