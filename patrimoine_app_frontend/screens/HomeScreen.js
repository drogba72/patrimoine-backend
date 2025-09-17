import React from "react";
import {
  View,
  Text,
  StyleSheet,
  FlatList,
  TouchableOpacity,
  Dimensions,
  ScrollView,
  useWindowDimensions,
} from "react-native";
import { Ionicons } from "@expo/vector-icons";
import { PieChart } from "react-native-chart-kit";

export default function HomeScreen({ navigation }) {
  const total = 525000;
  const data = [
    { name: "Actions", amount: 162750, color: "#0077b6" },
    { name: "Immobilier", amount: 152250, color: "#00b4d8" },
    { name: "Livret", amount: 126000, color: "#90e0ef" },
    { name: "Autres", amount: 84000, color: "#ffd166" },
  ];

  const chartData = data.map((item) => ({
    name: item.name,
    population: item.amount,
    color: item.color,
    legendFontColor: "#333",
    legendFontSize: 14,
  }));

  // Récupération dynamique des dimensions de la fenêtre
  const { width: screenWidth } = useWindowDimensions();

  return (
    <ScrollView
      contentContainerStyle={styles.container}
      showsVerticalScrollIndicator={false}
    >
      {/* Header */}
      <View style={styles.header}>
        <Text style={styles.title}>Patrimoine</Text>
        <Ionicons
          name="settings-outline"
          size={Math.min(screenWidth * 0.07, 30)} // taille dynamique de l'icône
          onPress={() => navigation.navigate("Paramètres")}
        />
      </View>

      {/* Total */}
      <Text style={[styles.total, { fontSize: Math.min(screenWidth * 0.08, 28) }]}>
        {total.toLocaleString("fr-FR")} €
      </Text>

      {/* Chart */}
      <PieChart
        data={chartData}
        width={screenWidth - 40} // marges conservées, largeur adaptative
        height={screenWidth / 2} // hauteur proportionnelle à la largeur écran
        chartConfig={{
          backgroundColor: "#fff",
          backgroundGradientFrom: "#fff",
          backgroundGradientTo: "#fff",
          color: (opacity = 1) => `rgba(0,0,0,${opacity})`,
        }}
        accessor={"population"}
        backgroundColor={"transparent"}
        paddingLeft={"10"}
        center={[0, 0]}
        absolute={false}
        hasLegend={true}
      />

      {/* List */}
      <FlatList
        data={data}
        keyExtractor={(item) => item.name}
        scrollEnabled={false} // désactive scroll interne, car ScrollView parent gère scroll
        renderItem={({ item }) => (
          <TouchableOpacity
            style={styles.item}
            onPress={() => navigation.navigate("Répartition")}
          >
            <View style={styles.itemRow}>
              <Ionicons
                name={
                  item.name === "Actions"
                    ? "trending-up-outline"
                    : item.name === "Immobilier"
                    ? "home-outline"
                    : item.name === "Livret"
                    ? "cash-outline"
                    : "ellipsis-horizontal-outline"
                }
                size={Math.min(screenWidth * 0.06, 22)} // taille adaptative icône
                color="#333"
              />
              <Text style={[styles.itemText, { fontSize: Math.min(screenWidth * 0.045, 16) }]}>
                {item.name}
              </Text>
            </View>
            <Text style={[styles.itemValue, { fontSize: Math.min(screenWidth * 0.045, 16) }]}>
              +{item.amount.toLocaleString("fr-FR")} €
            </Text>
          </TouchableOpacity>
        )}
      />
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  container: {
    padding: 20,
    paddingBottom: 40,
    backgroundColor: "#fff",
    flexGrow: 1,
  },
  header: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
  },
  title: { fontWeight: "600", fontSize: 22 },
  total: {
    fontWeight: "bold",
    marginVertical: 20,
  },
  item: {
    flexDirection: "row",
    justifyContent: "space-between",
    paddingVertical: 12,
    borderBottomWidth: 1,
    borderBottomColor: "#eee",
  },
  itemRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: 10,
  },
  itemText: {},
  itemValue: {
    fontWeight: "600",
    color: "#333",
  },
});
