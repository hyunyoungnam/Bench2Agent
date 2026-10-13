"""Paper identity in normalize: official ids and feed links merge, titles only
propose candidates, conflicts merge nothing, and the result does not depend
on input order."""
import random
import unittest

from icml.normalize import dedupe_key, identify, merge_duplicates, official_id


def row(eid, title, authors, *, paper_url=None, sourceid=None, uid=None, eventtype="Poster", sourceurl="https://openreview.net/group?id=X/Conference", abstract="a", topic=None):
    from icml.normalize import split_topic, topic_origin
    rec = {"id": eid, "name": title, "paper_url": paper_url, "sourceid": sourceid, "uid": uid, "eventtype": eventtype,
           "sourceurl": sourceurl, "authors": [{"fullname": a} for a in authors], "topic": topic}
    area, sub = split_topic(topic)
    return {"_key": dedupe_key(rec), "_rec": rec, "_source_ids": [], "event_id": eid, "title": title, "authors": list(authors),
            "abstract": abstract, "presentation": eventtype, "is_oral": eventtype == "Oral", "is_spotlight": False,
            "area": area, "subarea": sub, "topic_raw": topic, "topic_origin": topic_origin(sourceurl, topic)}


class IdentityTests(unittest.TestCase):
    def groups(self, rows):
        parts = identify([dict(r) for r in rows])
        return sorted(sorted(r["event_id"] for r in g) for g in parts)

    def test_official_id_merges_even_when_the_title_changed(self):
        a = row(1, "Characterizing Agents in Production", ["A B", "C D"], paper_url="https://openreview.net/forum?id=mWxEAgz3xu")
        b = row(2, "Measuring Agents in Production", ["A B", "C D"], paper_url="https://openreview.net/forum?id=mWxEAgz3xu", eventtype="Oral")
        self.assertEqual(self.groups([a, b]), [[1, 2]])
        merged = merge_duplicates(identify([dict(a), dict(b)])[0])
        self.assertEqual(merged["identity_rule"], "merged by openreview")
        self.assertEqual(merged["titles"], ["Characterizing Agents in Production", "Measuring Agents in Production"])
        self.assertEqual([s["event_id"] for s in merged["schedule_rows"]], [1, 2])

    def test_synthetic_forum_id_is_not_an_official_id_but_the_feed_link_merges(self):
        a = row(27888, "AlphaEdit: Null-Space Constrained Knowledge Editing", ["A B"], paper_url="https://openreview.net/forum?id=vo9t20wsmd", sourceid=4871, uid="u1")
        b = row(31741, "AlphaEdit: Null-Space Constrained Model Editing", ["A B"], paper_url="https://openreview.net/forum?id=2025-Oral--4871-29ca3cc8", sourceid=-4871, uid="u1", eventtype="Oral")
        self.assertIsNone(official_id(b["_rec"]))
        self.assertEqual(self.groups([a, b]), [[27888, 31741]])
        self.assertEqual(merge_duplicates(identify([dict(a), dict(b)])[0])["identity_rule"], "merged by sourceid+uid")

    def test_same_title_without_an_id_link_is_a_candidate_not_a_merge(self):
        a = row(1, "[Re] Explaining in Style", ["Chase van de Geijn", "Victor K"], sourceid=11)
        b = row(2, "[Re] Explaining in Style", ["Noah van der Vleuten", "Tadija R"], sourceid=12)
        self.assertEqual(self.groups([a, b]), [[1], [2]])
        parts = identify([dict(a), dict(b)])
        for g in parts:
            m = merge_duplicates(g)
            self.assertEqual(m["identity_candidates"][0]["reason"], "same title")
        # the same team under one title twice is still not merged by the title: separate papers, candidates
        c = row(3, "[Re] Explaining in Style", ["Chase van de Geijn"], sourceid=13, eventtype="Oral")
        self.assertEqual(self.groups([a, c]), [[1], [3]])

    def test_id_conflict_merges_nothing_and_is_recorded(self):
        # measured at NeurIPS 2022: two reproducibility reports by different teams share one bibliography URL
        a = row(1, "Paper one", ["A B"], sourceid=5, paper_url="https://rescience.github.io/bibliography/Petcu_2022.html", sourceurl="2022_rescience")
        b = row(2, "Paper two", ["X Y"], sourceid=-5, paper_url="https://rescience.github.io/bibliography/Petcu_2022.html", sourceurl="2022_rescience")
        self.assertEqual(self.groups([a, b]), [[1], [2]])
        parts = identify([dict(a), dict(b)])
        self.assertTrue(all(merge_duplicates(g).get("identity_conflict") for g in parts))

    def test_rows_without_authors_merge_only_through_an_id(self):
        a = row(1, "Same title", ["A B"], sourceid=7)
        b = row(2, "Same title", [], sourceid=-7, eventtype="Oral")        # feed link, no authors: merges
        c = row(3, "Same title", [], sourceid=99)                           # title only: stays apart
        self.assertEqual(self.groups([a, b, c]), [[1, 2], [3]])

    def test_feed_ids_are_scoped_to_the_track_and_unknown_tracks_join_the_only_one(self):
        main = "https://openreview.net/group?id=NeurIPS.cc/2025/Conference"
        db = "https://openreview.net/group?id=NeurIPS.cc/2025/Datasets_and_Benchmarks_Track"
        # NeurIPS 2025 reuses sourceid 1118 in two tracks: two papers, no link, no conflict
        a = row(115161, "Alligat0R", ["A B"], sourceid=1118, sourceurl=main)
        b = row(121776, "FAVOR-Bench", ["X Y"], sourceid=1118, sourceurl=db)
        self.assertEqual(self.groups([a, b]), [[115161], [121776]])
        self.assertFalse(any(merge_duplicates(g).get("identity_conflict") for g in identify([dict(a), dict(b)])))
        # a satellite listing of the same track and an Oral row whose track is unknown both link
        c = row(130310, "Alligat0R", ["A B"], sourceid=-1118, sourceurl="cdmx-" + main + "-cdmx", eventtype="Oral")
        self.assertEqual(self.groups([a, b, c]), [[115161, 130310], [121776]])
        d = row(115, "Bi-directional Contextual Attention", ["C D"], sourceid=-2804, sourceurl="None", eventtype="Oral")
        e = row(114, "Bi-directional Contextual Attention", ["C D"], sourceid=2804, sourceurl="ECCV2024")
        self.assertEqual(self.groups([d, e]), [[114, 115]])
        # a feed that names no track at all still links by its sourceid (ICLR 2021, NeurIPS 2020)
        g = row(2527, "What Matters", ["E F"], sourceid=581, sourceurl=None)
        h = row(3534, "What Matters", ["E F"], sourceid=-581, sourceurl=None, eventtype="Oral")
        self.assertEqual(self.groups([g, h]), [[2527, 3534]])
        # an unknown-track row whose sourceid lives in two tracks stays alone
        f = row(999, "Something", ["A B"], sourceid=-1118, sourceurl=None, eventtype="Oral")
        self.assertEqual(self.groups([a, b, f]), [[999], [115161], [121776]])

    def test_merged_record_keeps_the_topic_and_its_origin_from_whichever_row_has_it(self):
        poster = row(1, "T", ["A B"], sourceid=9, topic="Deep Learning->Robotics")
        oral = row(2, "T", ["A B"], sourceid=-9, eventtype="Oral")            # the Oral row carries no topic
        merged = merge_duplicates(identify([dict(poster), dict(oral)])[0])
        self.assertEqual((merged["event_id"], merged["area"], merged["subarea"], merged["topic_origin"]), (2, "Deep Learning", "Robotics", "declared"))

    def test_result_is_independent_of_input_order(self):
        base = [row(1, "T1", ["A B"], paper_url="https://openreview.net/forum?id=X1", sourceid=1),
                row(2, "T1 (retitled)", ["A B"], paper_url="https://openreview.net/forum?id=X1", sourceid=-1, eventtype="Oral"),
                row(3, "T2", ["C D"], sourceid=2), row(4, "T2", ["C D"], sourceid=-2, eventtype="Oral"),
                row(5, "T3", ["E F"], sourceid=3), row(6, "T3", ["G H"], sourceid=4),
                row(7, "T4", [], sourceid=5), row(8, "T4", ["I J"], sourceid=6)]
        expected = self.groups(base)
        self.assertEqual(expected, [[1, 2], [3, 4], [5], [6], [7], [8]])
        reps = sorted(merge_duplicates(g)["event_id"] for g in identify([dict(r) for r in base]))
        self.assertEqual(reps, [2, 4, 5, 6, 7, 8])                         # an Oral row represents its paper
        rng = random.Random(0)
        for _ in range(20):
            shuffled = list(base)
            rng.shuffle(shuffled)
            self.assertEqual(self.groups(shuffled), expected)
            parts = identify([dict(r) for r in shuffled])
            self.assertEqual(sorted(merge_duplicates(g)["event_id"] for g in parts), reps)   # representatives do not move either


if __name__ == "__main__":
    unittest.main()
