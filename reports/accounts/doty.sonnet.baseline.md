# Account extraction baseline

- Ground truth: `reports/accounts/doty.ground-truth.yaml`
- Prediction: `reports/accounts/doty.sonnet.yaml`
- Boundary source: `../ingests/store/868395b7e8a1f005c309f865abc637b573cfd56907204bec18f799c151ba648d.v2.md`
- Matching: deterministic maximum-cardinality, maximum-score one-to-one assignment; boundaries rank only candidates that pass semantic matching
- Semantic threshold: 0.28
- Precision: 0.75 (15/20)
- Recall: 0.3846 (15/39)
- Boundary overlap: 0.5295 mean intersection-over-union (14/15 matches measurable)
- Claim-binding coverage: 0.7107 (280/394)
- Resolvable claim-binding coverage: 0.7198 (280/389)

## Matches

| Gold | Predicted | Semantic | Matching evidence | Boundary IoU | Assignment score |
|---|---|---:|---|---:|---:|
| The Mario Woods interrogation at Ellsworth Air Force Base, 1977 | Mario Woods's craft and being encounter and subsequent OSI debrief | 0.5185 | air, force, mario, policeman, security, sergeant, wood | 0.12 | 0.6385 |
| The Myrna Hansen report and the two DIA agents who visited Kirtland | Myrna Hansen abduction case and the DIA agents who revealed a dedicated abduction investigation unit | 0.4706 | agent, dia, hansen, myrna | 0.6667 | 1.1373 |
| The Paul Bennewitz deception operation and the drone programme it concealed | Paul Bennewitz disinformation operation over drone sightings mistaken for UFOs | 0.4706 | bennewitz, drone, operation, paul | 0.8784 | 1.3490 |
| The Sandia security guard and the craft that landed over a Kirtland weapons bunker | Sandia guard's encounter with a landed craft and two entities at a nuclear weapons bunker | 0.6316 | bunker, craft, guard, laboratorie, landed, national, sandia, security, weapon | 0.0421 | 0.6737 |
| The indoctrination briefing into the programme Doty calls NineQ, with the Roswell and Kingman recovery films | Film shown at the briefing depicting the Roswell crash recovery | 0.4706 | briefing, classified, film, recovery, roswell | 0.025 | 0.4956 |
| Recruiting and handling Bill Moore inside the UFO research groups | Bill Moore's role feeding disinformation into UFO groups on behalf of counterintelligence | 0.4706 | bill, group, moore, ufo | 0.2353 | 0.7059 |
| Showing Linda Moulton Howe a presidential briefing document at Kirtland | Linda Howe's HBO documentary briefing and the Pentagon officer who leaked to her | 0.3529 | briefing, howe, linda, moulton | 0.5854 | 0.9383 |
| The paid prostitute informants around the Nellis range and the F-117 major who talked | Prostitutes recruited near Nellis to inform on Air Force personnel discussing classified test flights | 0.5294 | 117, f, flying, major, nelli, prostitute, range, test, training | 0.974 | 1.5034 |
| The taping of the UFO Cover-Up television special in which Doty appeared as Falcon | The 1987 UFO Cover-Up Live television special deception operation (Falcon and Condor) | 0.5455 | alia, cover, doty, falcon, special, television, ufo, up | 0.5347 | 1.0802 |
| The disc found in a mine shaft, pulled out by the DART team and stored at Tonopah | Recovery of a small disc-shaped craft from a mine shaft | 0.3529 | disc, found, mine, miner, shaft | 0.8554 | 1.2083 |
| The repeatedly abducted Air Force sergeant at Kirtland | Air Force sergeant's repeated abductions and thwarted taking near Kirtland | 0.5714 | air, force, kirtland, sergeant, whistleblower | n/a | 0.5714 |
| How the Alien Interview film was smuggled out of Groom Lake | The Air Force sergeant who smuggled the Alien Interview footage out of Groom Lake | 0.6316 | air, alien, audiovisual, force, groom, interview, lake, out, sergeant, smuggled, unnamed | 0.6818 | 1.3134 |
| The captain's wife taken from Interstate 25 near Springer and found at Eagle Nest Lake | Captain's dependent wife taken from the roadside near Springer | 0.5000 | captain, dependent, springer, taken, wife | 0.9333 | 1.4333 |
| The A-7D captain who photographed a craft while fishing and lost thirteen hours | A-7D National Guard pilot's fishing encounter and missing time near a northern New Mexico lake | 0.2963 | 7d, air, fishing, force, pilot | 0.8365 | 1.1328 |
| The man who walked into a rock crevice near Sedona and never came out | A man who walked through a rock crevice near Sedona and never returned | 0.7500 | be, claiming, crevice, man, never, rock, sedona, time, unnamed, walked | 0.044 | 0.7940 |

## Missed

- The FBI visits and the OSI phone call after Doty began speaking publicly
- The three uniformed Air Force officers at the 2019 Laughlin UFO convention
- The New Mexico Air National Guard A-7 pilot with contact in the air and on the ground
- Pressuring two Nevada MUFON investigators away from a close-encounter witness
- The MJ-12 document reaching Jaime Shandera and the seizure of Bill Moore's copy in San Francisco
- Colonel Law's remark about the MJ-12 documents at a UFO convention
- The civilian who saw the Aurora prototype and was given an invented UFO sighting
- Chris Ramsay's account of David Copperfield's father's 1945 Roswell identity card
- Digging up a South Dakota rancher's buried craft for Hal Puthoff's institute
- The night callout to a crash near Warm Springs, Nevada, and the driver who had hauled round things before
- The night the surveillance operation prevented the sergeant's abduction
- The footage cut from the film Disclosure Day and the private Palm Springs screening
- The passengers taken to the S2 Annex and the van crash near Rachel
- The failed attempt to reach the two Carthusian monks at the Vermont monastery
- Seeing a living extraterrestrial on a closed-circuit monitor and having to report it
- The submarine crewman who took a piece of something and gave it to a Kirtland contractor
- The Space Force approaches and the Defense Inspector General's questions about the 1981 report
- The case-file list on the internet and the freedom-of-information flood it caused
- The pilot who fired two Sidewinders at a craft that repaired itself
- Watching the fabric of space open at the Nevada Test Site
- The Lancaster, Pennsylvania Civil War photograph with a jet in the background
- The Air Force team that met an extraterrestrial walking along a road inside the Nellis range
- Doty's two truths and a lie
- The object that followed the Sandia Peak tram, 1980

## Extra

- Michael Johnson's hospitalization and separate interrogation
- The 1979 briefing on US government contact with extraterrestrials since 1947
- Film shown at the briefing depicting a second recovery near Kingman, Arizona
- Carthusian monks recruited as interpreters for extraterrestrials at S2 Annex
- A close 1965 high school wrestling match against a state runner-up

## Machine-readable result

```json
{
  "method": {
    "matching": "deterministic maximum-cardinality, maximum-score one-to-one assignment; boundaries rank only candidates that pass semantic matching",
    "semantic_threshold": 0.28
  },
  "counts": {
    "gold": 39,
    "predicted": 20,
    "matched": 15
  },
  "precision": 0.75,
  "recall": 0.3846,
  "boundary_overlap": {
    "available": 14,
    "matched": 15,
    "mean_iou": 0.5295
  },
  "claim_binding": {
    "bound": 280,
    "unbindable": 5,
    "outside": 109,
    "total": 394,
    "coverage": 0.7107,
    "resolvable_coverage": 0.7198
  },
  "matches": [
    {
      "gold_index": 0,
      "predicted_index": 1,
      "score": 0.6385,
      "title_score": 0.25,
      "subject_score": 0.5185,
      "lexical_score": 0.3306,
      "semantic_score": 0.5185,
      "shared_title_terms": [
        "mario",
        "wood"
      ],
      "shared_subject_terms": [
        "air",
        "force",
        "mario",
        "policeman",
        "security",
        "sergeant",
        "wood"
      ],
      "boundary_overlap": {
        "gold": [
          46,
          159
        ],
        "predicted": [
          35,
          60
        ],
        "intersection_lines": 15,
        "iou": 0.12
      },
      "eligible": true,
      "gold_title": "The Mario Woods interrogation at Ellsworth Air Force Base, 1977",
      "predicted_title": "Mario Woods's craft and being encounter and subsequent OSI debrief"
    },
    {
      "gold_index": 1,
      "predicted_index": 7,
      "score": 1.1373,
      "title_score": 0.4706,
      "subject_score": 0.1818,
      "lexical_score": 0.384,
      "semantic_score": 0.4706,
      "shared_title_terms": [
        "agent",
        "dia",
        "hansen",
        "myrna"
      ],
      "shared_subject_terms": [
        "hansen",
        "myrna"
      ],
      "boundary_overlap": {
        "gold": [
          245,
          282
        ],
        "predicted": [
          244,
          270
        ],
        "intersection_lines": 26,
        "iou": 0.6667
      },
      "eligible": true,
      "gold_title": "The Myrna Hansen report and the two DIA agents who visited Kirtland",
      "predicted_title": "Myrna Hansen abduction case and the DIA agents who revealed a dedicated abduction investigation unit"
    },
    {
      "gold_index": 2,
      "predicted_index": 8,
      "score": 1.349,
      "title_score": 0.4706,
      "subject_score": 0.1739,
      "lexical_score": 0.3816,
      "semantic_score": 0.4706,
      "shared_title_terms": [
        "bennewitz",
        "drone",
        "operation",
        "paul"
      ],
      "shared_subject_terms": [
        "bennewitz",
        "paul"
      ],
      "boundary_overlap": {
        "gold": [
          301,
          374
        ],
        "predicted": [
          301,
          365
        ],
        "intersection_lines": 65,
        "iou": 0.8784
      },
      "eligible": true,
      "gold_title": "The Paul Bennewitz deception operation and the drone programme it concealed",
      "predicted_title": "Paul Bennewitz disinformation operation over drone sightings mistaken for UFOs"
    },
    {
      "gold_index": 3,
      "predicted_index": 3,
      "score": 0.6737,
      "title_score": 0.6316,
      "subject_score": 0.3226,
      "lexical_score": 0.5389,
      "semantic_score": 0.6316,
      "shared_title_terms": [
        "bunker",
        "craft",
        "guard",
        "landed",
        "sandia",
        "weapon"
      ],
      "shared_subject_terms": [
        "guard",
        "laboratorie",
        "national",
        "sandia",
        "security"
      ],
      "boundary_overlap": {
        "gold": [
          383,
          905
        ],
        "predicted": [
          383,
          404
        ],
        "intersection_lines": 22,
        "iou": 0.0421
      },
      "eligible": true,
      "gold_title": "The Sandia security guard and the craft that landed over a Kirtland weapons bunker",
      "predicted_title": "Sandia guard's encounter with a landed craft and two entities at a nuclear weapons bunker"
    },
    {
      "gold_index": 4,
      "predicted_index": 5,
      "score": 0.4956,
      "title_score": 0.4706,
      "subject_score": 0.1379,
      "lexical_score": 0.3708,
      "semantic_score": 0.4706,
      "shared_title_terms": [
        "briefing",
        "film",
        "recovery",
        "roswell"
      ],
      "shared_subject_terms": [
        "classified",
        "film"
      ],
      "boundary_overlap": {
        "gold": [
          394,
          753
        ],
        "predicted": [
          474,
          482
        ],
        "intersection_lines": 9,
        "iou": 0.025
      },
      "eligible": true,
      "gold_title": "The indoctrination briefing into the programme Doty calls NineQ, with the Roswell and Kingman recovery films",
      "predicted_title": "Film shown at the briefing depicting the Roswell crash recovery"
    },
    {
      "gold_index": 8,
      "predicted_index": 12,
      "score": 0.7059,
      "title_score": 0.4706,
      "subject_score": 0.2,
      "lexical_score": 0.3894,
      "semantic_score": 0.4706,
      "shared_title_terms": [
        "bill",
        "group",
        "moore",
        "ufo"
      ],
      "shared_subject_terms": [
        "bill",
        "moore"
      ],
      "boundary_overlap": {
        "gold": [
          1052,
          1085
        ],
        "predicted": [
          1067,
          1074
        ],
        "intersection_lines": 8,
        "iou": 0.2353
      },
      "eligible": true,
      "gold_title": "Recruiting and handling Bill Moore inside the UFO research groups",
      "predicted_title": "Bill Moore's role feeding disinformation into UFO groups on behalf of counterintelligence"
    },
    {
      "gold_index": 12,
      "predicted_index": 11,
      "score": 0.9383,
      "title_score": 0.3529,
      "subject_score": 0.2609,
      "lexical_score": 0.3253,
      "semantic_score": 0.3529,
      "shared_title_terms": [
        "briefing",
        "howe",
        "linda"
      ],
      "shared_subject_terms": [
        "howe",
        "linda",
        "moulton"
      ],
      "boundary_overlap": {
        "gold": [
          1290,
          1370
        ],
        "predicted": [
          1289,
          1337
        ],
        "intersection_lines": 48,
        "iou": 0.5854
      },
      "eligible": true,
      "gold_title": "Showing Linda Moulton Howe a presidential briefing document at Kirtland",
      "predicted_title": "Linda Howe's HBO documentary briefing and the Pentagon officer who leaked to her"
    },
    {
      "gold_index": 14,
      "predicted_index": 17,
      "score": 1.5034,
      "title_score": 0.1905,
      "subject_score": 0.5294,
      "lexical_score": 0.2922,
      "semantic_score": 0.5294,
      "shared_title_terms": [
        "nelli",
        "prostitute"
      ],
      "shared_subject_terms": [
        "117",
        "f",
        "flying",
        "major",
        "nelli",
        "prostitute",
        "range",
        "test",
        "training"
      ],
      "boundary_overlap": {
        "gold": [
          1400,
          1551
        ],
        "predicted": [
          1402,
          1553
        ],
        "intersection_lines": 150,
        "iou": 0.974
      },
      "eligible": true,
      "gold_title": "The paid prostitute informants around the Nellis range and the F-117 major who talked",
      "predicted_title": "Prostitutes recruited near Nellis to inform on Air Force personnel discussing classified test flights"
    },
    {
      "gold_index": 15,
      "predicted_index": 13,
      "score": 1.0802,
      "title_score": 0.5455,
      "subject_score": 0.1875,
      "lexical_score": 0.4381,
      "semantic_score": 0.5455,
      "shared_title_terms": [
        "cover",
        "falcon",
        "special",
        "television",
        "ufo",
        "up"
      ],
      "shared_subject_terms": [
        "alia",
        "doty",
        "falcon"
      ],
      "boundary_overlap": {
        "gold": [
          1561,
          1660
        ],
        "predicted": [
          1560,
          1614
        ],
        "intersection_lines": 54,
        "iou": 0.5347
      },
      "eligible": true,
      "gold_title": "The taping of the UFO Cover-Up television special in which Doty appeared as Falcon",
      "predicted_title": "The 1987 UFO Cover-Up Live television special deception operation (Falcon and Condor)"
    },
    {
      "gold_index": 18,
      "predicted_index": 16,
      "score": 1.2083,
      "title_score": 0.3529,
      "subject_score": 0.125,
      "lexical_score": 0.2846,
      "semantic_score": 0.3529,
      "shared_title_terms": [
        "disc",
        "mine",
        "shaft"
      ],
      "shared_subject_terms": [
        "found",
        "miner"
      ],
      "boundary_overlap": {
        "gold": [
          1919,
          2000
        ],
        "predicted": [
          1918,
          1989
        ],
        "intersection_lines": 71,
        "iou": 0.8554
      },
      "eligible": true,
      "gold_title": "The disc found in a mine shaft, pulled out by the DART team and stored at Tonopah",
      "predicted_title": "Recovery of a small disc-shaped craft from a mine shaft"
    },
    {
      "gold_index": 20,
      "predicted_index": 0,
      "score": 0.5714,
      "title_score": 0.5714,
      "subject_score": 0.2667,
      "lexical_score": 0.48,
      "semantic_score": 0.5714,
      "shared_title_terms": [
        "air",
        "force",
        "kirtland",
        "sergeant"
      ],
      "shared_subject_terms": [
        "air",
        "force",
        "sergeant",
        "whistleblower"
      ],
      "boundary_overlap": null,
      "eligible": true,
      "gold_title": "The repeatedly abducted Air Force sergeant at Kirtland",
      "predicted_title": "Air Force sergeant's repeated abductions and thwarted taking near Kirtland"
    },
    {
      "gold_index": 25,
      "predicted_index": 15,
      "score": 1.3134,
      "title_score": 0.6316,
      "subject_score": 0.4,
      "lexical_score": 0.5621,
      "semantic_score": 0.6316,
      "shared_title_terms": [
        "alien",
        "groom",
        "interview",
        "lake",
        "out",
        "smuggled"
      ],
      "shared_subject_terms": [
        "air",
        "audiovisual",
        "force",
        "sergeant",
        "unnamed"
      ],
      "boundary_overlap": {
        "gold": [
          3313,
          3355
        ],
        "predicted": [
          3312,
          3342
        ],
        "intersection_lines": 30,
        "iou": 0.6818
      },
      "eligible": true,
      "gold_title": "How the Alien Interview film was smuggled out of Groom Lake",
      "predicted_title": "The Air Force sergeant who smuggled the Alien Interview footage out of Groom Lake"
    },
    {
      "gold_index": 27,
      "predicted_index": 9,
      "score": 1.4333,
      "title_score": 0.5,
      "subject_score": 0.2222,
      "lexical_score": 0.4167,
      "semantic_score": 0.5,
      "shared_title_terms": [
        "captain",
        "springer",
        "taken",
        "wife"
      ],
      "shared_subject_terms": [
        "captain",
        "dependent",
        "wife"
      ],
      "boundary_overlap": {
        "gold": [
          3530,
          3678
        ],
        "predicted": [
          3529,
          3669
        ],
        "intersection_lines": 140,
        "iou": 0.9333
      },
      "eligible": true,
      "gold_title": "The captain's wife taken from Interstate 25 near Springer and found at Eagle Nest Lake",
      "predicted_title": "Captain's dependent wife taken from the roadside near Springer"
    },
    {
      "gold_index": 32,
      "predicted_index": 10,
      "score": 1.1328,
      "title_score": 0.1905,
      "subject_score": 0.2963,
      "lexical_score": 0.2222,
      "semantic_score": 0.2963,
      "shared_title_terms": [
        "7d",
        "fishing"
      ],
      "shared_subject_terms": [
        "7d",
        "air",
        "force",
        "pilot"
      ],
      "boundary_overlap": {
        "gold": [
          4028,
          4185
        ],
        "predicted": [
          4027,
          4160
        ],
        "intersection_lines": 133,
        "iou": 0.8365
      },
      "eligible": true,
      "gold_title": "The A-7D captain who photographed a craft while fishing and lost thirteen hours",
      "predicted_title": "A-7D National Guard pilot's fishing encounter and missing time near a northern New Mexico lake"
    },
    {
      "gold_index": 33,
      "predicted_index": 18,
      "score": 0.794,
      "title_score": 0.75,
      "subject_score": 0.3571,
      "lexical_score": 0.6321,
      "semantic_score": 0.75,
      "shared_title_terms": [
        "crevice",
        "man",
        "never",
        "rock",
        "sedona",
        "walked"
      ],
      "shared_subject_terms": [
        "be",
        "claiming",
        "man",
        "time",
        "unnamed"
      ],
      "boundary_overlap": {
        "gold": [
          4205,
          4295
        ],
        "predicted": [
          4210,
          4213
        ],
        "intersection_lines": 4,
        "iou": 0.044
      },
      "eligible": true,
      "gold_title": "The man who walked into a rock crevice near Sedona and never came out",
      "predicted_title": "A man who walked through a rock crevice near Sedona and never returned"
    }
  ],
  "missed": [
    {
      "gold_index": 5,
      "title": "The FBI visits and the OSI phone call after Doty began speaking publicly"
    },
    {
      "gold_index": 6,
      "title": "The three uniformed Air Force officers at the 2019 Laughlin UFO convention"
    },
    {
      "gold_index": 7,
      "title": "The New Mexico Air National Guard A-7 pilot with contact in the air and on the ground"
    },
    {
      "gold_index": 9,
      "title": "Pressuring two Nevada MUFON investigators away from a close-encounter witness"
    },
    {
      "gold_index": 10,
      "title": "The MJ-12 document reaching Jaime Shandera and the seizure of Bill Moore's copy in San Francisco"
    },
    {
      "gold_index": 11,
      "title": "Colonel Law's remark about the MJ-12 documents at a UFO convention"
    },
    {
      "gold_index": 13,
      "title": "The civilian who saw the Aurora prototype and was given an invented UFO sighting"
    },
    {
      "gold_index": 16,
      "title": "Chris Ramsay's account of David Copperfield's father's 1945 Roswell identity card"
    },
    {
      "gold_index": 17,
      "title": "Digging up a South Dakota rancher's buried craft for Hal Puthoff's institute"
    },
    {
      "gold_index": 19,
      "title": "The night callout to a crash near Warm Springs, Nevada, and the driver who had hauled round things before"
    },
    {
      "gold_index": 21,
      "title": "The night the surveillance operation prevented the sergeant's abduction"
    },
    {
      "gold_index": 22,
      "title": "The footage cut from the film Disclosure Day and the private Palm Springs screening"
    },
    {
      "gold_index": 23,
      "title": "The passengers taken to the S2 Annex and the van crash near Rachel"
    },
    {
      "gold_index": 24,
      "title": "The failed attempt to reach the two Carthusian monks at the Vermont monastery"
    },
    {
      "gold_index": 26,
      "title": "Seeing a living extraterrestrial on a closed-circuit monitor and having to report it"
    },
    {
      "gold_index": 28,
      "title": "The submarine crewman who took a piece of something and gave it to a Kirtland contractor"
    },
    {
      "gold_index": 29,
      "title": "The Space Force approaches and the Defense Inspector General's questions about the 1981 report"
    },
    {
      "gold_index": 30,
      "title": "The case-file list on the internet and the freedom-of-information flood it caused"
    },
    {
      "gold_index": 31,
      "title": "The pilot who fired two Sidewinders at a craft that repaired itself"
    },
    {
      "gold_index": 34,
      "title": "Watching the fabric of space open at the Nevada Test Site"
    },
    {
      "gold_index": 35,
      "title": "The Lancaster, Pennsylvania Civil War photograph with a jet in the background"
    },
    {
      "gold_index": 36,
      "title": "The Air Force team that met an extraterrestrial walking along a road inside the Nellis range"
    },
    {
      "gold_index": 37,
      "title": "Doty's two truths and a lie"
    },
    {
      "gold_index": 38,
      "title": "The object that followed the Sandia Peak tram, 1980"
    }
  ],
  "extra": [
    {
      "predicted_index": 2,
      "id": "acct-michael-johnson-was-in-another-room.",
      "title": "Michael Johnson's hospitalization and separate interrogation"
    },
    {
      "predicted_index": 4,
      "id": "acct-so-i-did.-i-went-over-there.",
      "title": "The 1979 briefing on US government contact with extraterrestrials since 1947"
    },
    {
      "predicted_index": 6,
      "id": "acct-they-also-showed-a-second-uh-recovery-site-in-kingman-arizona",
      "title": "Film shown at the briefing depicting a second recovery near Kingman, Arizona"
    },
    {
      "predicted_index": 14,
      "id": "acct-i-knew-of-an-operation-that-was-occurring-at-groom-lake.",
      "title": "Carthusian monks recruited as interpreters for extraterrestrials at S2 Annex"
    },
    {
      "predicted_index": 19,
      "id": "acct-in-about-1965-uh-i",
      "title": "A close 1965 high school wrestling match against a state runner-up"
    }
  ]
}
```
