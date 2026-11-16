const http = require('http');
const movies = [
  {
    "id": 157336,
    "title": "Interstellar",
    "original_title": "Interstellar",
    "release_date": "2014-01-01",
    "poster_path": "/yQvGrMoipbRoddT0ZR8tPoR7NfX.jpg",
    "overview": "The adventures of a group of explorers who make use of a newly discovered wormhole to surpass the limitations on human space travel and conquer the vast distances involved in an interstellar voyage.",
    "vote_average": 8.477,
    "genres": [
      {
        "id": 1,
        "name": "Adventure"
      },
      {
        "id": 2,
        "name": "Drama"
      },
      {
        "id": 3,
        "name": "Science Fiction"
      }
    ],
    "credits": {
      "crew": [
        {
          "job": "Director",
          "name": "Christopher Nolan"
        }
      ]
    },
    "keywords": {
      "keywords": [
        {
          "name": "spacecraft"
        },
        {
          "name": "race against time"
        },
        {
          "name": "artificial intelligence (a.i.)"
        },
        {
          "name": "nasa"
        },
        {
          "name": "time warp"
        },
        {
          "name": "dystopia"
        },
        {
          "name": "expedition"
        },
        {
          "name": "time travel"
        },
        {
          "name": "space travel"
        },
        {
          "name": "wormhole"
        },
        {
          "name": "famine"
        },
        {
          "name": "hibernation"
        }
      ]
    },
    "adult": false
  },
  {
    "id": 496243,
    "title": "Parasite",
    "original_title": "Parasite",
    "release_date": "2019-01-01",
    "poster_path": "/7IiTTgloJzvGI1TAYymCfbfl3vT.jpg",
    "overview": "All unemployed, Ki-taek's family takes peculiar interest in the wealthy and glamorous Parks for their livelihood until they get entangled in an unexpected incident.",
    "vote_average": 8.5,
    "genres": [
      {
        "id": 1,
        "name": "Comedy"
      },
      {
        "id": 2,
        "name": "Thriller"
      },
      {
        "id": 3,
        "name": "Drama"
      }
    ],
    "credits": {
      "crew": [
        {
          "job": "Director",
          "name": "Bong Joon Ho"
        }
      ]
    },
    "keywords": {
      "keywords": [
        {
          "name": "dark comedy"
        },
        {
          "name": "con artist"
        },
        {
          "name": "social commentary"
        },
        {
          "name": "working class"
        },
        {
          "name": "infiltration"
        },
        {
          "name": "psychological thriller"
        },
        {
          "name": "class differences"
        },
        {
          "name": "family"
        },
        {
          "name": "unemployed"
        },
        {
          "name": "domestic worker"
        },
        {
          "name": "wealthy family"
        },
        {
          "name": "south korea"
        }
      ]
    },
    "adult": false
  },
  {
    "id": 550,
    "title": "Fight Club",
    "original_title": "Fight Club",
    "release_date": "1999-01-01",
    "poster_path": "/jSziioSwPVrOy9Yow3XhWIBDjq1.jpg",
    "overview": "A ticking-time-bomb insomniac and a slippery soap salesman channel primal male aggression into a shocking new form of therapy. Their concept catches on, with underground \"fight clubs\" forming in every town, until an eccentric gets in the way and ignites an out-of-control spiral toward oblivion.",
    "vote_average": 8.438,
    "genres": [
      {
        "id": 1,
        "name": "Drama"
      },
      {
        "id": 2,
        "name": "Thriller"
      }
    ],
    "credits": {
      "crew": [
        {
          "job": "Director",
          "name": "David Fincher"
        }
      ]
    },
    "keywords": {
      "keywords": [
        {
          "name": "dual identity"
        },
        {
          "name": "rage and hate"
        },
        {
          "name": "based on novel or book"
        },
        {
          "name": "nihilism"
        },
        {
          "name": "fight"
        },
        {
          "name": "support group"
        },
        {
          "name": "dystopia"
        },
        {
          "name": "insomnia"
        },
        {
          "name": "alter ego"
        },
        {
          "name": "breaking the fourth wall"
        },
        {
          "name": "split personality"
        },
        {
          "name": "quitting a job"
        }
      ]
    },
    "adult": false
  },
  {
    "id": 976893,
    "title": "Perfect Days",
    "original_title": "Perfect Days",
    "release_date": "2023-01-01",
    "poster_path": "/mjEk5Wwx6TYVqw29zSaUHclMIgp.jpg",
    "overview": "Hirayama is content with his life as a toilet cleaner in Tokyo. Outside of his structured routine, he cherishes music on cassette tapes, books, and taking photos of trees. Through unexpected encounters, he reflects on finding beauty in the world.",
    "vote_average": 7.792,
    "genres": [
      {
        "id": 1,
        "name": "Drama"
      }
    ],
    "credits": {
      "crew": [
        {
          "job": "Director",
          "name": "Wim Wenders"
        }
      ]
    },
    "keywords": {
      "keywords": [
        {
          "name": "tree"
        },
        {
          "name": "routine"
        },
        {
          "name": "slice of life"
        },
        {
          "name": "janitor"
        },
        {
          "name": "working class"
        },
        {
          "name": "tokyo, japan"
        },
        {
          "name": "character study"
        },
        {
          "name": "uncle niece relationship"
        },
        {
          "name": "audio cassette"
        },
        {
          "name": "solitude"
        },
        {
          "name": "mystical quest"
        },
        {
          "name": "human nature"
        }
      ]
    },
    "adult": false
  },
  {
    "id": 27205,
    "title": "Inception",
    "original_title": "Inception",
    "release_date": "2010-01-01",
    "poster_path": "/xlaY2zyzMfkhk0HSC5VUwzoZPU1.jpg",
    "overview": "Cobb, a skilled thief who commits corporate espionage by infiltrating the subconscious of his targets is offered a chance to regain his old life as payment for a task considered to be impossible: \"inception\", the implantation of another person's idea into a target's subconscious.",
    "vote_average": 8.372,
    "genres": [
      {
        "id": 1,
        "name": "Action"
      },
      {
        "id": 2,
        "name": "Science Fiction"
      },
      {
        "id": 3,
        "name": "Adventure"
      }
    ],
    "credits": {
      "crew": [
        {
          "job": "Director",
          "name": "Christopher Nolan"
        }
      ]
    },
    "keywords": {
      "keywords": [
        {
          "name": "mission"
        },
        {
          "name": "dreams"
        },
        {
          "name": "kidnapping"
        },
        {
          "name": "spy"
        },
        {
          "name": "allegory"
        },
        {
          "name": "intellectual"
        },
        {
          "name": "manipulation"
        },
        {
          "name": "heist"
        },
        {
          "name": "memory"
        },
        {
          "name": "dream world"
        },
        {
          "name": "subconscious"
        },
        {
          "name": "high concept"
        }
      ]
    },
    "adult": false
  },
  {
    "id": 155,
    "title": "The Dark Knight",
    "original_title": "The Dark Knight",
    "release_date": "2008-01-01",
    "poster_path": "/qJ2tW6WMUDux911r6m7haRef0WH.jpg",
    "overview": "Batman raises the stakes in his war on crime. With the help of Lt. Jim Gordon and District Attorney Harvey Dent, Batman sets out to dismantle the remaining criminal organizations that plague the streets. The partnership proves to be effective, but they soon find themselves prey to a reign of chaos unleashed by a rising criminal mastermind known to the terrified citizens of Gotham as the Joker.",
    "vote_average": 8.53,
    "genres": [
      {
        "id": 1,
        "name": "Action"
      },
      {
        "id": 2,
        "name": "Crime"
      },
      {
        "id": 3,
        "name": "Thriller"
      }
    ],
    "credits": {
      "crew": [
        {
          "job": "Director",
          "name": "Christopher Nolan"
        }
      ]
    },
    "keywords": {
      "keywords": [
        {
          "name": "sadism"
        },
        {
          "name": "chaos"
        },
        {
          "name": "secret identity"
        },
        {
          "name": "crime fighter"
        },
        {
          "name": "superhero"
        },
        {
          "name": "anti hero"
        },
        {
          "name": "scarecrow"
        },
        {
          "name": "based on comic"
        },
        {
          "name": "vigilante"
        },
        {
          "name": "organized crime"
        },
        {
          "name": "tragic hero"
        },
        {
          "name": "anti villain"
        }
      ]
    },
    "adult": false
  },
  {
    "id": 120467,
    "title": "The Grand Budapest Hotel",
    "original_title": "The Grand Budapest Hotel",
    "release_date": "2014-01-01",
    "poster_path": "/eWdyYQreja6JGCzqHWXpWHDrrPo.jpg",
    "overview": "The Grand Budapest Hotel tells of a legendary concierge at a famous European hotel between the wars and his friendship with a young employee who becomes his trusted protégé. The story involves the theft and recovery of a priceless Renaissance painting, the battle for an enormous family fortune and the slow and then sudden upheavals that transformed Europe during the first half of the 20th century.",
    "vote_average": 8.028,
    "genres": [
      {
        "id": 1,
        "name": "Comedy"
      },
      {
        "id": 2,
        "name": "Drama"
      }
    ],
    "credits": {
      "crew": [
        {
          "job": "Director",
          "name": "Wes Anderson"
        }
      ]
    },
    "keywords": {
      "keywords": [
        {
          "name": "hotel"
        },
        {
          "name": "painting"
        },
        {
          "name": "wartime"
        },
        {
          "name": "eastern europe"
        },
        {
          "name": "author"
        },
        {
          "name": "gunfight"
        },
        {
          "name": "theft"
        },
        {
          "name": "period drama"
        },
        {
          "name": "bellhop  "
        },
        {
          "name": "mentor protégé relationship"
        },
        {
          "name": "european"
        },
        {
          "name": "hotel lobby"
        }
      ]
    },
    "adult": false
  },
  {
    "id": 680,
    "title": "Pulp Fiction",
    "original_title": "Pulp Fiction",
    "release_date": "1994-01-01",
    "poster_path": null,
    "overview": "",
    "vote_average": 7.5,
    "genres": [],
    "credits": {
      "crew": [
        {
          "job": "Director",
          "name": "Quentin Tarantino"
        }
      ]
    },
    "keywords": {
      "keywords": [
        {
          "name": "drug dealer"
        },
        {
          "name": "drug crime"
        },
        {
          "name": "gangster"
        },
        {
          "name": "dark comedy"
        },
        {
          "name": "stolen money"
        },
        {
          "name": "crime boss"
        },
        {
          "name": "redemption"
        },
        {
          "name": "los angeles, california"
        },
        {
          "name": "theft"
        },
        {
          "name": "couple"
        },
        {
          "name": "nonlinear timeline"
        },
        {
          "name": "multiple storylines"
        }
      ]
    },
    "adult": false
  },
  {
    "id": 244786,
    "title": "Whiplash",
    "original_title": "Whiplash",
    "release_date": "2014-01-01",
    "poster_path": "/7fn624j5lj3xTme2SgiLCeuedmO.jpg",
    "overview": "Under the direction of a ruthless instructor, a talented young drummer begins to pursue perfection at any cost, even his humanity.",
    "vote_average": 8.374,
    "genres": [
      {
        "id": 1,
        "name": "Drama"
      },
      {
        "id": 2,
        "name": "Music"
      },
      {
        "id": 3,
        "name": "Thriller"
      }
    ],
    "credits": {
      "crew": [
        {
          "job": "Director",
          "name": "Damien Chazelle"
        }
      ]
    },
    "keywords": {
      "keywords": [
        {
          "name": "new york city"
        },
        {
          "name": "jazz"
        },
        {
          "name": "musician"
        },
        {
          "name": "obsession"
        },
        {
          "name": "music teacher"
        },
        {
          "name": "conservatory"
        },
        {
          "name": "drums"
        },
        {
          "name": "montage"
        },
        {
          "name": "perfectionist"
        },
        {
          "name": "public humiliation"
        },
        {
          "name": "jazz band"
        },
        {
          "name": "angry"
        }
      ]
    },
    "adult": false
  },
  {
    "id": 329865,
    "title": "Arrival",
    "original_title": "Arrival",
    "release_date": "2016-01-01",
    "poster_path": "/pEzNVQfdzYDzVK0XqxERIw2x2se.jpg",
    "overview": "Taking place after alien crafts land around the world, an expert linguist is recruited by the military to determine whether they come in peace or are a threat.",
    "vote_average": 7.626,
    "genres": [
      {
        "id": 1,
        "name": "Drama"
      },
      {
        "id": 2,
        "name": "Science Fiction"
      },
      {
        "id": 3,
        "name": "Mystery"
      }
    ],
    "credits": {
      "crew": [
        {
          "job": "Director",
          "name": "Denis Villeneuve"
        }
      ]
    },
    "keywords": {
      "keywords": [
        {
          "name": "spacecraft"
        },
        {
          "name": "extraterrestrial technology"
        },
        {
          "name": "loss"
        },
        {
          "name": "time"
        },
        {
          "name": "alien"
        },
        {
          "name": "language"
        },
        {
          "name": "female protagonist"
        },
        {
          "name": "scientist"
        },
        {
          "name": "heartbreak"
        },
        {
          "name": "based on short story"
        },
        {
          "name": "alien contact"
        },
        {
          "name": "military"
        }
      ]
    },
    "adult": false
  },
  {
    "id": 129,
    "title": "Spirited Away",
    "original_title": "Spirited Away",
    "release_date": "2001-01-01",
    "poster_path": null,
    "overview": "",
    "vote_average": 7.5,
    "genres": [],
    "credits": {
      "crew": [
        {
          "job": "Director",
          "name": "Hayao Miyazaki"
        }
      ]
    },
    "keywords": {
      "keywords": [
        {
          "name": "witch"
        },
        {
          "name": "parent child relationship"
        },
        {
          "name": "darkness"
        },
        {
          "name": "bath house"
        },
        {
          "name": "magic"
        },
        {
          "name": "spirit"
        },
        {
          "name": "parallel world"
        },
        {
          "name": "amusement park"
        },
        {
          "name": "youkai"
        },
        {
          "name": "japanese mythology"
        },
        {
          "name": "anime"
        }
      ]
    },
    "adult": false
  },
  {
    "id": 1398,
    "title": "Stalker",
    "original_title": "Stalker",
    "release_date": "1979-01-01",
    "poster_path": "/1qhOyf5C4s9ZdvY8d5JDx9DFMeT.jpg",
    "overview": "Near a gray and unnamed city is the Zone, a place guarded by barbed wire and soldiers, and where the normal laws of physics are victim to frequent anomalies. A stalker guides two men into the Zone, specifically to an area in which deep-seated desires are granted.",
    "vote_average": 8.1,
    "genres": [
      {
        "id": 1,
        "name": "Science Fiction"
      },
      {
        "id": 2,
        "name": "Drama"
      }
    ],
    "credits": {
      "crew": [
        {
          "job": "Director",
          "name": "Andrei Tarkovsky"
        }
      ]
    },
    "keywords": {
      "keywords": [
        {
          "name": "based on novel or book"
        },
        {
          "name": "guard"
        },
        {
          "name": "wish"
        },
        {
          "name": "stalker"
        },
        {
          "name": "alien"
        },
        {
          "name": "strugatsky"
        },
        {
          "name": "writer"
        },
        {
          "name": "soldier"
        },
        {
          "name": "existentialism"
        },
        {
          "name": "zone"
        },
        {
          "name": "complex"
        },
        {
          "name": "beautifully filmed"
        }
      ]
    },
    "adult": false
  },
  {
    "id": 603,
    "title": "The Matrix",
    "original_title": "The Matrix",
    "release_date": "1999-01-01",
    "poster_path": "/aOIuZAjPaRIE6CMzbazvcHuHXDc.jpg",
    "overview": "Set in the 22nd century, The Matrix tells the story of a computer hacker who joins a group of underground insurgents fighting the vast and powerful computers who now rule the earth.",
    "vote_average": 8.245,
    "genres": [
      {
        "id": 1,
        "name": "Action"
      },
      {
        "id": 2,
        "name": "Science Fiction"
      }
    ],
    "credits": {
      "crew": [
        {
          "job": "Director",
          "name": "Lana Wachowski"
        }
      ]
    },
    "keywords": {
      "keywords": [
        {
          "name": "man vs machine"
        },
        {
          "name": "martial arts"
        },
        {
          "name": "kung fu"
        },
        {
          "name": "dreams"
        },
        {
          "name": "fortune teller"
        },
        {
          "name": "artificial intelligence (a.i.)"
        },
        {
          "name": "saving the world"
        },
        {
          "name": "hacker"
        },
        {
          "name": "self sacrifice"
        },
        {
          "name": "virtual reality"
        },
        {
          "name": "fight"
        },
        {
          "name": "prophecy"
        }
      ]
    },
    "adult": false
  },
  {
    "id": 238,
    "title": "The Godfather",
    "original_title": "The Godfather",
    "release_date": "1972-01-01",
    "poster_path": "/3bhkrj58Vtu7enYsRolD1fZdja1.jpg",
    "overview": "Spanning the years 1945 to 1955, a chronicle of the fictional Italian-American Corleone crime family. When organized crime family patriarch, Vito Corleone barely survives an attempt on his life, his youngest son, Michael steps in to take care of the would-be killers, launching a campaign of bloody revenge.",
    "vote_average": 8.687,
    "genres": [
      {
        "id": 1,
        "name": "Drama"
      },
      {
        "id": 2,
        "name": "Crime"
      }
    ],
    "credits": {
      "crew": [
        {
          "job": "Director",
          "name": "Francis Ford Coppola"
        }
      ]
    },
    "keywords": {
      "keywords": [
        {
          "name": "based on novel or book"
        },
        {
          "name": "loss of loved one"
        },
        {
          "name": "love at first sight"
        },
        {
          "name": "italy"
        },
        {
          "name": "gangster"
        },
        {
          "name": "symbolism"
        },
        {
          "name": "patriarch"
        },
        {
          "name": "europe"
        },
        {
          "name": "organized crime"
        },
        {
          "name": "mafia"
        },
        {
          "name": "religion"
        },
        {
          "name": "revenge motive"
        }
      ]
    },
    "adult": false
  },
  {
    "id": 194,
    "title": "Amélie",
    "original_title": "Amélie",
    "release_date": "2001-01-01",
    "poster_path": "/nSxDa3M9aMvGVLoItzWTepQ5h5d.jpg",
    "overview": "At a tiny Parisian café, the adorable yet painfully shy Amélie accidentally discovers a gift for helping others. Soon Amelie is spending her days as a matchmaker, guardian angel, and all-around do-gooder. But when she bumps into a handsome stranger, will she find the courage to become the star of her very own love story?",
    "vote_average": 7.921,
    "genres": [
      {
        "id": 1,
        "name": "Comedy"
      },
      {
        "id": 2,
        "name": "Romance"
      }
    ],
    "credits": {
      "crew": [
        {
          "job": "Director",
          "name": "Jean-Pierre Jeunet"
        }
      ]
    },
    "keywords": {
      "keywords": [
        {
          "name": "daughter"
        },
        {
          "name": "paris, france"
        },
        {
          "name": "love triangle"
        },
        {
          "name": "photography"
        },
        {
          "name": "ghost train"
        },
        {
          "name": "sex shop"
        },
        {
          "name": "shyness"
        },
        {
          "name": "journey around the world"
        },
        {
          "name": "postcard"
        },
        {
          "name": "romcom"
        },
        {
          "name": "hypochondriac"
        },
        {
          "name": "romance"
        }
      ]
    },
    "adult": false
  },
  {
    "id": 37165,
    "title": "The Truman Show",
    "original_title": "The Truman Show",
    "release_date": "1998-01-01",
    "poster_path": "/vuza0WqY239yBXOadKlGwJsZJFE.jpg",
    "overview": "In a picture-perfect seaside town, an insurance salesman begins to realize that his entire existence may be staged and observed by a vast unseen audience as part of a long-running real-time reality TV show.",
    "vote_average": 8.152,
    "genres": [
      {
        "id": 1,
        "name": "Comedy"
      },
      {
        "id": 2,
        "name": "Drama"
      }
    ],
    "credits": {
      "crew": [
        {
          "job": "Director",
          "name": "Peter Weir"
        }
      ]
    },
    "keywords": {
      "keywords": [
        {
          "name": "escape"
        },
        {
          "name": "paranoia"
        },
        {
          "name": "dystopia"
        },
        {
          "name": "suspicion"
        },
        {
          "name": "video surveillance"
        },
        {
          "name": "deception"
        },
        {
          "name": "hidden camera"
        },
        {
          "name": "simulated reality "
        },
        {
          "name": "television producer"
        },
        {
          "name": "tv show in film"
        },
        {
          "name": "make believe"
        },
        {
          "name": "actor"
        }
      ]
    },
    "adult": false
  },
  {
    "id": 278,
    "title": "The Shawshank Redemption",
    "original_title": "The Shawshank Redemption",
    "release_date": "1994-01-01",
    "poster_path": "/9cqNxx0GxF0bflZmeSMuL5tnGzr.jpg",
    "overview": "Imprisoned in the 1940s for the double murder of his wife and her lover, upstanding banker Andy Dufresne begins a new life at the Shawshank prison, where he puts his accounting skills to work for an amoral warden. During his long stretch in prison, Dufresne comes to be admired by the other inmates -- including an older prisoner named Red -- for his integrity and unquenchable sense of hope.",
    "vote_average": 8.722,
    "genres": [
      {
        "id": 1,
        "name": "Drama"
      },
      {
        "id": 2,
        "name": "Crime"
      }
    ],
    "credits": {
      "crew": [
        {
          "job": "Director",
          "name": "Frank Darabont"
        }
      ]
    },
    "keywords": {
      "keywords": [
        {
          "name": "prison"
        },
        {
          "name": "friendship"
        },
        {
          "name": "police brutality"
        },
        {
          "name": "corruption"
        },
        {
          "name": "based on novel or book"
        },
        {
          "name": "freedom"
        },
        {
          "name": "hope"
        },
        {
          "name": "prison cell"
        },
        {
          "name": "delinquent"
        },
        {
          "name": "redemption"
        },
        {
          "name": "parole board"
        },
        {
          "name": "prison escape"
        }
      ]
    },
    "adult": false
  },
  {
    "id": 120,
    "title": "The Lord of the Rings: The Fellowship of the Ring",
    "original_title": "The Lord of the Rings: The Fellowship of the Ring",
    "release_date": "2001-01-01",
    "poster_path": "/6oom5QYQ2yQTMJIbnvbkBL9cHo6.jpg",
    "overview": "Young hobbit Frodo Baggins, after inheriting a mysterious ring from his uncle Bilbo, must leave his home in order to keep it from falling into the hands of its evil creator. Along the way, a fellowship is formed to protect the ringbearer and make sure that the ring arrives at its final destination: Mt. Doom, the only place where it can be destroyed.",
    "vote_average": 8.434,
    "genres": [
      {
        "id": 1,
        "name": "Adventure"
      },
      {
        "id": 2,
        "name": "Fantasy"
      },
      {
        "id": 3,
        "name": "Action"
      }
    ],
    "credits": {
      "crew": [
        {
          "job": "Director",
          "name": "Peter Jackson"
        }
      ]
    },
    "keywords": {
      "keywords": [
        {
          "name": "based on novel or book"
        },
        {
          "name": "elves"
        },
        {
          "name": "dwarf"
        },
        {
          "name": "magic"
        },
        {
          "name": "obsession"
        },
        {
          "name": "allegory"
        },
        {
          "name": "bravery"
        },
        {
          "name": "castle"
        },
        {
          "name": "troll"
        },
        {
          "name": "battle"
        },
        {
          "name": "demon"
        },
        {
          "name": "uncle nephew relationship"
        }
      ]
    },
    "adult": false
  },
  {
    "id": 62,
    "title": "2001: A Space Odyssey",
    "original_title": "2001: A Space Odyssey",
    "release_date": "1968-01-01",
    "poster_path": "/ve72VxNqjGM69Uky4WTo2bK6rfq.jpg",
    "overview": "Humanity finds a mysterious object buried beneath the lunar surface and sets off to find its origins with the help of HAL 9000, the world's most advanced super computer.",
    "vote_average": 8.051,
    "genres": [
      {
        "id": 1,
        "name": "Science Fiction"
      },
      {
        "id": 2,
        "name": "Mystery"
      },
      {
        "id": 3,
        "name": "Adventure"
      }
    ],
    "credits": {
      "crew": [
        {
          "job": "Director",
          "name": "Stanley Kubrick"
        }
      ]
    },
    "keywords": {
      "keywords": [
        {
          "name": "man vs machine"
        },
        {
          "name": "moon"
        },
        {
          "name": "jupiter"
        },
        {
          "name": "artificial intelligence (a.i.)"
        },
        {
          "name": "based on novel or book"
        },
        {
          "name": "technology"
        },
        {
          "name": "super computer"
        },
        {
          "name": "space travel"
        },
        {
          "name": "space mission"
        },
        {
          "name": "moon base"
        },
        {
          "name": "astronaut"
        },
        {
          "name": "evolution"
        }
      ]
    },
    "adult": false
  },
  {
    "id": 38,
    "title": "Eternal Sunshine of the Spotless Mind",
    "original_title": "Eternal Sunshine of the Spotless Mind",
    "release_date": "2004-01-01",
    "poster_path": "/5MwkWH9tYHv3mV9OdYTMR5qreIz.jpg",
    "overview": "Joel Barish, heartbroken that his girlfriend underwent a procedure to erase him from her memory, decides to do the same. However, as he watches his memories of her fade away, he realises that he still loves her, and may be too late to correct his mistake.",
    "vote_average": 8.1,
    "genres": [
      {
        "id": 1,
        "name": "Science Fiction"
      },
      {
        "id": 2,
        "name": "Drama"
      },
      {
        "id": 3,
        "name": "Romance"
      }
    ],
    "credits": {
      "crew": [
        {
          "job": "Director",
          "name": "Michel Gondry"
        }
      ]
    },
    "keywords": {
      "keywords": [
        {
          "name": "new york city"
        },
        {
          "name": "regret"
        },
        {
          "name": "jealousy"
        },
        {
          "name": "deja vu"
        },
        {
          "name": "amnesia"
        },
        {
          "name": "dreams"
        },
        {
          "name": "operation"
        },
        {
          "name": "relationship problems"
        },
        {
          "name": "love"
        },
        {
          "name": "memory"
        },
        {
          "name": "brainwashing"
        },
        {
          "name": "relationship"
        }
      ]
    },
    "adult": false
  }
];
http.createServer(async (req,res) => {
  const url = new URL(req.url, 'http://mock');
  res.setHeader('Content-Type','application/json');
  if (url.pathname === '/3/search/movie' || url.pathname === '/3/movie/popular') {
    const q = (url.searchParams.get('query') || '').toLocaleLowerCase();
    const results = movies.filter(m => !q || m.title.toLocaleLowerCase().includes(q));
    return res.end(JSON.stringify({page:1,results,total_pages:1,total_results:results.length}));
  }
  const id = /^\/3\/movie\/(\d+)$/.exec(url.pathname);
  if (id) {
    const film = movies.find(m => m.id === Number(id[1]));
    if (film) return res.end(JSON.stringify(film));
    res.statusCode=404;return res.end(JSON.stringify({status_code:34,status_message:'The resource you requested could not be found.'}));
  }
  res.statusCode=501;res.end(JSON.stringify({error:'Not modeled by native catalog simulation'}));
}).listen(process.env.PORT || 4518);
