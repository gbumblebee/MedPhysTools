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
| Aucun DICOM hors de l'onglet DICOM | `tests/test_interface_photos.py::test_un_dicom_n_entre_pas_dans_l_archive` | Vérifié dans l'interface ; l'API Python `importer_copie` ne le refuse pas |
| Import sans option : pseudonymisé | `tests/test_dicom_import.py::test_un_import_sans_option_particuliere_est_pseudonymise` | Vérifié sur fichiers synthétiques |
| Fantôme identique octet pour octet | `tests/test_dicom_import.py::test_un_fichier_fantome_reste_identique` | Vérifié |
| Fantôme qui ressemble à un patient : avertissement | `tests/test_interface_dicom.py::test_un_fantome_suspect_demande_confirmation_et_peut_etre_annule` | Vérifié |
| Aucune balise de la liste dans la copie | `tests/test_dicom_import.py::test_aucune_balise_de_la_liste_ne_reste_dans_la_copie` | Vérifié |
| Aucun octet identifiant dans la copie | `tests/test_dicom_import.py::test_aucun_octet_identifiant_ne_reste_dans_la_copie` | Vérifié sur fichiers synthétiques |
| Aucun nom de fichier dans `page.json`, Git ou l'index | `tests/test_dicom_import.py::test_aucun_nom_de_fichier_n_atteint_l_archive` | Vérifié |
| Pixels identiques, empreinte comprise | `tests/test_dicom_import.py::test_les_pixels_restent_identiques_empreinte_comprise` | Vérifié |
| Limite du texte incrusté affichée | `tests/test_interface_dicom.py::test_l_onglet_s_ouvre_avec_ses_commandes_visibles` | Vérifié |
| CT : pente et ordonnée | `tests/test_volumes.py::test_le_scanner_applique_pente_et_ordonnee` | Vérifié |
| Dose : DoseGridScaling | `tests/test_volumes.py::test_la_dose_multiplie_les_entiers_par_le_dose_grid_scaling` | Vérifié |
| TEP : pente propre à chaque coupe | `tests/test_volumes.py::test_chaque_coupe_de_tep_a_sa_propre_pente` | Vérifié |
| TEMP : valeurs et positions | `tests/test_volumes.py::test_la_temp_lit_la_pente_reelle_et_les_positions_du_detecteur` | Vérifié |
| 4D en fichiers séparés | `tests/test_volumes.py::test_une_serie_4d_en_fichiers_separes_se_range_par_phase_et_par_coupe` | Vérifié |
| 4D en multi-images amélioré | `tests/test_volumes.py::test_une_serie_4d_amelioree_lit_tout_image_par_image` | Vérifié |
| Formats compressés | `tests/test_volumes.py::test_une_serie_compressee_donne_les_memes_valeurs` | Vérifié : RLE, JPEG 2000 et JPEG-LS sans perte |
| Orientation radiologique | `tests/test_volumes.py::test_un_patient_acquis_en_sens_inverse_reste_bien_oriente` | Vérifié |
| Structures sur une coupe | `tests/test_volumes.py::test_en_coronal_un_contour_axial_donne_le_segment_attendu` | Vérifié |
| Valeur physique lue à la souris | `tests/test_interface_dicom.py::test_la_souris_lit_la_valeur_physique_et_son_unite` | Vérifié |
| Fichier non pris en charge : message clair | `tests/test_interface_dicom.py::test_un_fichier_non_pris_en_charge_s_explique_sans_planter` | Vérifié |
| Décodage impossible : message clair | `tests/test_interface_dicom.py::test_un_decodage_impossible_s_explique_sans_planter` | Vérifié |
| Balises identifiantes non affichées | `tests/test_interface_dicom.py::test_les_balises_identifiantes_ne_s_affichent_jamais` | Vérifié |
| Grosse série sans bloquer l'interface | `tests/test_interface_dicom.py::test_une_grosse_serie_ne_bloque_pas_l_interface` | Vérifié avec une lecture ralentie ; pas encore sur une vraie grosse série |
| Cache des empreintes | `tests/test_dicom_import.py::test_le_cache_evite_de_relire_une_grosse_serie` | Vérifié |
| Une série importée apparaît dans l'onglet de fichiers | `tests/test_interface_dicom.py::test_une_serie_importee_apparait_dans_l_onglet_de_fichiers` | Vérifié |
| La retirer du DICOM la retire de l'onglet de fichiers | `tests/test_interface_dicom.py::test_retirer_la_serie_la_retire_aussi_de_l_onglet_de_fichiers` | Vérifié |
| Changer de série libère l'ancienne | `tests/test_interface_dicom.py::test_changer_de_serie_libere_vraiment_l_ancienne` | Vérifié |
| Un seul volume en mémoire entre plusieurs onglets | `tests/test_interface_dicom.py::test_deux_onglets_dicom_ne_gardent_qu_un_volume` | Vérifié |
| Très gros volume sur le disque | `tests/test_volumes.py::test_au_dela_du_seuil_le_volume_est_sur_le_disque` | Vérifié avec un seuil abaissé, pas encore sur un vrai volume de plus de 2 Go |
| Commentaire d'une pièce jointe | `tests/test_interface_photos.py::test_commenter_une_piece_jointe` | Vérifié |
| Import personnalisé de scalaires et d'informations | `tests/test_interface_tableau.py::test_un_import_complet_remplit_le_tableau_et_les_scalaires` | Vérifié |
| Une valeur importée sans incertitude ne s'enregistre pas | `tests/test_interface_tableau.py::test_un_scalaire_importe_sans_incertitude_reste_a_completer` | Vérifié |
| Un import n'écrase aucune mesure | `tests/test_interface_tableau.py::test_un_nom_deja_pris_n_ecrase_rien` | Vérifié |
| Fichier importé conservé en pièce jointe | `tests/test_interface_photos.py::test_le_fichier_importe_devient_une_piece_jointe` | Vérifié |
| Suppression d'une page tracée, numéro jamais réutilisé | `tests/test_archive.py::test_le_numero_d_une_page_supprimee_ne_resert_pas` | Vérifié |
| Réglages d'un graphe retrouvés après réouverture | `tests/test_graphes.py::test_les_reglages_se_retrouvent_apres_reouverture` | Vérifié |
| Barres d'erreur conformes au tableau, à k choisi | `tests/test_graphes.py::test_les_barres_dessinees_correspondent_au_tableau` | Vérifié |
| Graphe affiché sans OpenGL | `tests/test_graphes.py::test_un_graphe_s_affiche_sans_opengl` | Vérifié hors écran ; à confirmer sur un PC d'hôpital |
| Annotations à part de la photo | `tests/test_page.py::test_les_annotations_sont_enregistrees_a_part_de_la_photo` | Vérifié |
| Repère d'erreur commune conservé | `tests/test_page.py::test_l_erreur_commune_est_conservee_dans_une_page` | Vérifié |
| Rapport de deux mesures à certificat commun | `tests/test_calculs.py::test_le_rapport_garde_seulement_les_erreurs_propres` | Vérifié à la main |
| Formule de décroissance : valeur, U et budget | `tests/test_calculs.py::test_la_formule_de_decroissance_donne_valeur_u_et_budget` | Vérifié à la main |
| Une formule n'exécute pas de code | `tests/test_calculs.py::test_une_formule_interdite_est_refusee_sans_rien_executer` | Vérifié |
| Une dérivée garde sa formule sans remplacer de valeur brute | `tests/test_calculs.py::test_une_derivee_ne_remplace_aucune_valeur_brute` | Vérifié |
| Calcul entre colonnes, ligne par ligne, avec incertitude | `tests/test_calculs.py::test_une_colonne_se_calcule_ligne_par_ligne_avec_son_incertitude` | Vérifié à la main |
| Incertitude calculée d'une colonne, hors du CSV | `tests/test_interface_tableau.py::test_l_incertitude_calculee_s_affiche_et_ne_s_enregistre_pas_dans_le_csv` | Vérifié |
| Incertitude calculée propagée, sans incertitude propre | `tests/test_calculs.py::test_l_incertitude_calculee_se_propage_sans_incertitude_propre` | Vérifié |
| Incertitude calculée en barres de graphe | `tests/test_graphes.py::test_une_incertitude_calculee_donne_les_barres_du_graphe` | Vérifié |
| Formule d'incertitude fausse refusée | `tests/test_tableaux.py::test_une_formule_fausse_est_refusee_en_l_expliquant` | Vérifié |
| Titres dans les notes | `tests/test_tags.py::test_une_ligne_diese_devient_un_titre_de_son_niveau` | Vérifié |
| Équations dans les notes | `tests/test_tags.py::test_une_equation_devient_une_image` | Vérifié |
| Équation illisible marquée | `tests/test_tags.py::test_une_equation_illisible_reste_visible_et_marquee` | Vérifié |
| Équation réellement dessinée par Qt | `tests/test_interface_notes.py::test_l_equation_se_dessine_dans_l_apercu` | Vérifié |
| Équation dans le PDF | `tests/test_export.py::test_une_equation_passe_dans_le_pdf` | Vérifié |
| Titres dans le PDF | `tests/test_export.py::test_les_titres_et_l_incertitude_calculee_passent_dans_le_pdf` | Vérifié |
| Courbe dérivée, avec incertitude | `tests/test_calculs.py::test_la_derivee_donne_une_colonne_avec_son_incertitude` | Vérifié à la main |
| Courbe primitive, avec incertitude | `tests/test_calculs.py::test_la_primitive_cumule_les_trapezes_avec_son_incertitude` | Vérifié à la main |
| Courbe ajoutée depuis l'onglet de calculs | `tests/test_interface_calculs.py::test_le_bouton_ajoute_la_courbe_derivee` | Vérifié |
| Colonne calculée affichée dans chaque tableau concerné | `tests/test_interface_calculs.py::test_une_colonne_s_affiche_dans_tous_les_tableaux_concernes` | Vérifié |
| Une colonne calculée ne change pas les CSV bruts | `tests/test_interface_calculs.py::test_une_colonne_calculee_ne_touche_pas_au_csv` | Vérifié |
| Colonne calculée tracée avec ses barres | `tests/test_graphes.py::test_une_colonne_calculee_se_trace_avec_ses_barres` | Vérifié |
| Un ajustement exponentiel retrouve des paramètres connus | `tests/test_ajustements.py::test_un_ajustement_exponentiel_retrouve_des_parametres_connus` | Vérifié |
| Incertitude de la fonction ajustée, covariances comprises | `tests/test_ajustements.py::test_l_incertitude_de_la_droite_tient_compte_des_covariances` | Vérifié face à numpy et à Monte-Carlo |
| Incertitude sur x documentée et appliquée | `tests/test_ajustements.py::test_l_incertitude_sur_x_passe_par_la_variance_effective` | Vérifié |
| Coefficients et corrélation retrouvés à l'identique | `tests/test_ajustements.py::test_tout_se_retrouve_a_l_identique_apres_relecture` | Vérifié |
| Fonction réévaluée depuis les coefficients, même incertitude | `tests/test_ajustements.py::test_la_fonction_se_reevalue_depuis_les_coefficients_avec_la_meme_incertitude` | Vérifié |
| Courbe plus fine que les données, sans extrapolation | `tests/test_ajustements.py::test_la_courbe_est_plus_fine_que_les_donnees_et_reste_dans_leur_intervalle` | Vérifié |
| Indicateur relu avec méthode, données, intervalle et incertitude | `tests/test_ajustements.py::test_un_indicateur_se_relit_avec_sa_methode_ses_donnees_et_son_intervalle` | Vérifié |
| Ajustement et indicateur ne modifient aucune valeur brute | `tests/test_interface_ajustements.py::test_un_ajustement_ne_touche_a_aucune_valeur_brute` | Vérifié |
| Chaque indicateur vérifié sur une fonction connue | `tests/test_ajustements.py::test_chaque_indicateur_sur_la_parabole_ajustee` | Vérifié |
| Un tag de note suit la valeur, et la note ne garde que le tag | `tests/test_tags.py::test_la_note_garde_le_tag_et_l_affichage_suit_la_valeur` | Vérifié |
| Un tag cassé se voit | `tests/test_tags.py::test_un_tag_casse_se_voit_sans_arreter_le_reste` | Vérifié |
| Capture d'un graphe gardée dans la page et signalée si les données changent | `tests/test_interface_notes.py::test_une_capture_signale_que_le_tableau_a_change` | Vérifié |
| Export du cahier en PDF, sans logiciel pour le relire | `tests/test_export.py::test_le_pdf_contient_le_cahier` | Vérifié sous Fedora |
| L'export n'omet aucune grandeur | `tests/test_export.py::test_une_grandeur_hors_de_tout_onglet_reste_dans_le_cahier` | Vérifié |
| L'archive figée du format 1 se relit | `tests/test_format_1.py::test_toutes_les_pages_se_relisent` | Vérifié |

## Tutoriel de lecture

| Exigence | Test | État |
|---|---|---|
| Le tutoriel tourne du début à la fin | `tests/test_tutoriel.py::test_le_tutoriel_s_execute_du_debut_a_la_fin` | Vérifié |

## Gabarits dans l'interface

| Exigence | Test | État |
|---|---|---|
| Le gabarit créé depuis une page lui est conforme | `tests/test_interface_gabarit.py::test_le_gabarit_cree_depuis_une_page_lui_est_conforme` | Vérifié |
| Une page depuis un gabarit indique ce qu'il reste à remplir | `tests/test_interface_gabarit.py::test_une_page_depuis_un_gabarit_indique_ce_qu_il_reste` | Vérifié |
| Une unité incohérente est bloquée à la saisie | `tests/test_interface_gabarit.py::test_une_unite_incoherente_est_bloquee_a_la_saisie` | Vérifié |
| Validation refusée tant qu'il reste à remplir | `tests/test_interface_gabarit.py::test_la_validation_est_refusee_tant_qu_il_reste_a_remplir` | Vérifié |
| Notes, graphes et ajustements cochés entrent dans le gabarit | `tests/test_interface_gabarit.py::test_le_dialogue_coche_les_onglets_et_les_met_dans_le_gabarit` | Vérifié |
| Le gabarit ne garde aucun résultat d'ajustement | `tests/test_gabarit.py::test_le_gabarit_garde_les_reglages_sans_aucun_resultat` | Vérifié |
| Un graphe préparé se trace à la saisie, avec sa courbe | `tests/test_interface_gabarit.py::test_le_graphe_attend_ses_donnees_puis_se_trace_tout_seul` | Vérifié |
| Une page validée ne se réajuste jamais seule | `tests/test_interface_gabarit.py::test_une_page_validee_ne_se_reajuste_jamais_toute_seule` | Vérifié |

## Modifications hors du logiciel et chemin d'une page

| Exigence | Test | État |
|---|---|---|
| Ouvrir le logiciel crée la nouvelle version et le signale | `tests/test_modifications_externes.py::test_ouvrir_le_logiciel_cree_la_version_et_le_signale` | Vérifié |
| Le fichier est gardé tel quel, l'ancienne valeur reste lisible | `tests/test_modifications_externes.py::test_une_modification_a_la_main_devient_une_version_fidele` | Vérifié |
| Une archive saine ne crée aucune version | `tests/test_modifications_externes.py::test_ouvrir_une_archive_saine_n_affiche_rien_et_ne_cree_rien` | Vérifié |
| Relire depuis un script n'écrit rien | `tests/test_modifications_externes.py::test_relire_une_archive_depuis_un_script_n_ecrit_rien` | Vérifié |
| Une page validée modifiée est signalée, sa valeur barrée | `tests/test_modifications_externes.py::test_la_fiche_signale_une_page_validee_modifiee_et_barre_l_ancienne_valeur` | Vérifié |
| Le clic droit copie le chemin du dossier de la page | `tests/test_modifications_externes.py::test_le_menu_d_une_page_copie_son_chemin` | Vérifié |
| Le clic droit copie le chemin d'une pièce jointe | `tests/test_copie_chemin.py::test_une_piece_jointe_donne_son_chemin_dans_la_page` | Vérifié |
| Le clic droit copie le chemin d'une photo des notes | `tests/test_copie_chemin.py::test_une_photo_des_notes_donne_son_chemin_avant_tout_enregistrement` | Vérifié |
| Le clic droit copie le zip d'une série de l'onglet DICOM | `tests/test_copie_chemin.py::test_une_serie_de_l_onglet_dicom_donne_son_zip` | Vérifié |
| Une série restée sur le PACS dit qu'elle n'a pas de chemin | `tests/test_copie_chemin.py::test_une_serie_restee_sur_le_pacs_explique_qu_elle_n_a_pas_de_chemin` | Vérifié |
| La fiche d'une page copie aussi ces chemins | `tests/test_copie_chemin.py::test_la_fiche_donne_le_chemin_des_fichiers_et_des_photos` | Vérifié |
| Un enregistrement automatique ne ferme pas le menu d'une photo | `tests/test_copie_chemin.py::test_un_enregistrement_menu_ouvert_ne_casse_pas_le_menu_d_une_photo` | Vérifié |
| Afficher une photo n'ouvre aucune fenêtre parasite | `tests/test_copie_chemin.py::test_construire_une_vignette_n_ouvre_aucune_fenetre` | Vérifié |

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
| Vérification globale d'intégrité | Non implémentée ; contrôle à l'accès seulement | Phase 3 |
| Données réelles | Scénario à valeurs fictives | Premier CQ réel, en phase 1 |
