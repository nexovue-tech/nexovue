package setup

import "testing"

func TestTopicsAreSizedToTheBrokersThatExist(t *testing.T) {
	for _, tc := range []struct {
		brokers     int
		replication int16
		minInSync   string
	}{
		{1, 1, "1"},
		{2, 2, "1"},
		{3, 3, "2"},
		{5, 3, "2"},
	} {
		plan, err := PlanTopics(tc.brokers, DefaultTopicSettings)
		if err != nil {
			t.Fatalf("%d brokers: %v", tc.brokers, err)
		}
		for _, topic := range plan {
			if topic.Replication != tc.replication || topic.Configs["min.insync.replicas"] != tc.minInSync {
				t.Fatalf("%d brokers: %s has %d copies, min.insync %s", tc.brokers, topic.Name,
					topic.Replication, topic.Configs["min.insync.replicas"])
			}
		}
	}
}

func TestEveryTopicTheServicesUseIsPlanned(t *testing.T) {
	plan, err := PlanTopics(1, DefaultTopicSettings)
	if err != nil {
		t.Fatal(err)
	}
	partitions := map[string]int32{}
	for _, topic := range plan {
		partitions[topic.Name] = topic.Partitions
		if topic.Configs["compression.type"] != "zstd" || topic.Configs["max.message.bytes"] != "16781312" {
			t.Fatalf("%s configs %v", topic.Name, topic.Configs)
		}
	}
	want := map[string]int32{
		"spans-raw": 12, "logs-raw": 4, "metrics-raw": 4, "profiles-raw": 4,
		"spans-dlq": 1, "logs-dlq": 1, "metrics-dlq": 1, "profiles-dlq": 1,
	}
	if len(partitions) != len(want) {
		t.Fatalf("planned %v", partitions)
	}
	for name, count := range want {
		if partitions[name] != count {
			t.Fatalf("%s: %d partitions, want %d", name, partitions[name], count)
		}
	}
}

func TestNoBrokerIsAnError(t *testing.T) {
	if _, err := PlanTopics(0, DefaultTopicSettings); err == nil {
		t.Fatal("planned topics with no broker")
	}
}
